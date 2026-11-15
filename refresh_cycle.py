#!/usr/bin/env python3
"""Exercise a 7-colour Inky Impression with a long run of colour + pattern refreshes.

Stop the InkFrame service first, then run the same command on every panel you
want to keep in step:

    sudo systemctl stop inkframe
    ~/inky-env/bin/python refresh_cycle.py                 # 24 hours, one refresh every 2 minutes
    ~/inky-env/bin/python refresh_cycle.py --hours 12 --interval 90
    ~/inky-env/bin/python refresh_cycle.py --preview out/  # no panel needed: writes PNGs

How the units stay synchronised
-------------------------------
There is no networking between the panels. Each frame is a pure function of the
wall clock: slot = unix_time // interval, and the picture for a slot is derived
only from (slot, seed). Any Pi with an NTP-synced clock and the same --interval
and --seed therefore draws the same frame at the same moment, and a unit that is
started late or rebooted drops straight back into step.

What gets shown
---------------
Slots are grouped into blocks of 8: 7 picture frames, then the controller's
CLEAN pass. Within a block the same layout is shown 7 times with the colours
rotated one step each time, so by the end of the block every pixel has been
driven to each of the 7 colours exactly once - wear is even per pixel, whatever
the pattern looks like.

  * Every 4th block is the plain full-panel sweep: solid black, white, green,
    blue, red, yellow and orange (in a different order each time).
  * The other blocks walk a list of ~20 patterns (stripes, checkers, rings,
    noise, voronoi cells, random rectangles, ...) in a freshly shuffled order
    each lap, with random sizes and positions.
  * The colour order changes every block (a different starting colour and a
    different step round the 7), arranged so that over every 6 blocks each
    colour-to-colour transition is exercised equally often too.
"""

import argparse
import math
import os
import random
import signal
import sys
import time
import warnings
from datetime import datetime

import numpy as np
from PIL import Image

# The driver warns on every refresh while it waits for the panel; it's expected, not a fault.
warnings.filterwarnings("ignore", message="Busy Wait", category=UserWarning)

# Inky UC8159 colour indices
BLACK, WHITE, GREEN, BLUE, RED, YELLOW, ORANGE, CLEAN = range(8)
NUM_COLOURS = 7
COLOUR_NAMES = ["black", "white", "green", "blue", "red", "yellow", "orange", "clean"]

# Only used for --preview PNGs; the panel itself receives raw colour indices.
PREVIEW_RGB = [
    (28, 24, 28), (255, 255, 255), (29, 173, 35), (30, 29, 174),
    (205, 36, 37), (231, 222, 35), (216, 123, 36), (235, 225, 215),
]

BLOCK_LEN = NUM_COLOURS + 1  # 7 colour rotations + 1 CLEAN frame
SOLID_EVERY = 4              # every 4th block is the plain solid-colour sweep


# --------------------------------------------------------------------------
# Patterns. Each returns an (h, w) uint8 array of colour indices 0..6.
# They draw randomness only from rng.randrange / rng.getrandbits (never from
# numpy's generators) so that every Pi, whatever its Python/numpy version,
# produces identical output for the same seed.
# --------------------------------------------------------------------------

def _grid(w, h):
    return np.meshgrid(np.arange(w), np.arange(h))


def _random_cells(rows, cols, rng):
    """(rows, cols) array of random colour indices, cheap even for every pixel."""
    n = rows * cols
    raw = rng.getrandbits(8 * n).to_bytes(n, "little")
    return (np.frombuffer(raw, dtype=np.uint8).astype(np.int64) % NUM_COLOURS).reshape(rows, cols)


def pat_vstripes(w, h, rng):
    x, _ = _grid(w, h)
    return x // rng.randrange(4, 60)


def pat_hstripes(w, h, rng):
    _, y = _grid(w, h)
    return y // rng.randrange(4, 60)


def pat_colour_bars(w, h, rng):
    x, _ = _grid(w, h)
    return x * NUM_COLOURS // w


def pat_diagonal(w, h, rng):
    x, y = _grid(w, h)
    size = rng.randrange(8, 60)
    return (x + y * (1 if rng.randrange(2) else -1)) // size


def pat_checker(w, h, rng):
    x, y = _grid(w, h)
    size = rng.randrange(1, 50)  # includes very fine, near pixel-level checkers
    step = rng.randrange(1, NUM_COLOURS)
    return (x // size) + (y // size) * step


def pat_rings(w, h, rng):
    x, y = _grid(w, h)
    cx, cy = rng.randrange(w), rng.randrange(h)
    return (np.hypot(x - cx, y - cy) // rng.randrange(8, 40)).astype(np.int64)


def pat_wedges(w, h, rng):
    x, y = _grid(w, h)
    cx, cy = rng.randrange(w), rng.randrange(h)
    spokes = rng.randrange(1, 5) * NUM_COLOURS
    angle = np.arctan2(y - cy, x - cx) + math.pi
    return (angle / (2 * math.pi) * spokes).astype(np.int64)


def pat_tiles(w, h, rng):
    size = rng.randrange(8, 64)
    cols, rows = w // size + 1, h // size + 1
    cells = np.array([rng.randrange(NUM_COLOURS) for _ in range(cols * rows)])
    cells = cells.reshape(rows, cols)
    return np.repeat(np.repeat(cells, size, axis=0), size, axis=1)[:h, :w]


def pat_plasma(w, h, rng):
    x, y = _grid(w, h)
    f = [rng.randrange(15, 80) for _ in range(4)]
    v = (np.sin(x / f[0]) + np.sin(y / f[1])
         + np.sin((x + y) / f[2]) + np.sin(np.hypot(x - w / 2, y - h / 2) / f[3]))
    return ((v + 4) / 8 * NUM_COLOURS * 2).astype(np.int64)


def pat_waves(w, h, rng):
    x, y = _grid(w, h)
    amp, period, band = rng.randrange(10, 60), rng.randrange(30, 150), rng.randrange(10, 50)
    return ((y + amp * np.sin(x / period * 2 * math.pi)) // band).astype(np.int64)


def pat_quilt(w, h, rng):
    """Nested squares inside a grid of tiles."""
    x, y = _grid(w, h)
    tile, band = rng.randrange(60, 160), rng.randrange(4, 16)
    dx, dy = np.abs(x % tile - tile // 2), np.abs(y % tile - tile // 2)
    return np.maximum(dx, dy) // band + (x // tile) + (y // tile)


def pat_pinstripes(w, h, rng):
    """1-3 pixel lines, vertical or horizontal."""
    x, y = _grid(w, h)
    return (x if rng.randrange(2) else y) // rng.randrange(1, 4)


def pat_noise(w, h, rng):
    """Random colour per cell, from single pixels up to 6px speckle."""
    size = rng.randrange(1, 7)
    cells = _random_cells(h // size + 1, w // size + 1, rng)
    return np.repeat(np.repeat(cells, size, axis=0), size, axis=1)[:h, :w]


def pat_rectangles(w, h, rng):
    """Overlapping random rectangles of random colours."""
    out = np.full((h, w), rng.randrange(NUM_COLOURS), dtype=np.int64)
    for _ in range(rng.randrange(40, 160)):
        rw, rh = rng.randrange(10, w // 2), rng.randrange(10, h // 2)
        x0, y0 = rng.randrange(-rw // 2, w), rng.randrange(-rh // 2, h)
        out[max(0, y0):y0 + rh, max(0, x0):x0 + rw] = rng.randrange(NUM_COLOURS)
    return out


def pat_voronoi(w, h, rng):
    """Irregular cells around random points."""
    x, y = _grid(w, h)
    best = np.full((h, w), 1 << 40, dtype=np.int64)
    out = np.zeros((h, w), dtype=np.int64)
    for i in range(rng.randrange(12, 70)):
        px, py = rng.randrange(w), rng.randrange(h)
        d = (x - px) ** 2 + (y - py) ** 2
        closer = d < best
        best[closer] = d[closer]
        out[closer] = i
    return out


def pat_spiral(w, h, rng):
    x, y = _grid(w, h)
    cx, cy = rng.randrange(w), rng.randrange(h)
    arms = rng.randrange(1, 4) * NUM_COLOURS * (1 if rng.randrange(2) else -1)
    band = rng.randrange(10, 50)
    turn = (np.arctan2(y - cy, x - cx) + math.pi) / (2 * math.pi)
    return np.floor(np.hypot(x - cx, y - cy) / band + turn * arms).astype(np.int64)


def pat_dots(w, h, rng):
    """Grid of discs, each a different colour from its background."""
    x, y = _grid(w, h)
    cell = rng.randrange(16, 90)
    radius = cell * rng.randrange(25, 46) // 100
    step = rng.randrange(1, NUM_COLOURS)
    inside = (x % cell - cell // 2) ** 2 + (y % cell - cell // 2) ** 2 <= radius ** 2
    return x // cell + (y // cell) * step + inside * rng.randrange(1, NUM_COLOURS)


def pat_bricks(w, h, rng):
    x, y = _grid(w, h)
    bh = rng.randrange(8, 50)
    bw = bh * rng.randrange(2, 5)
    row = y // bh
    return (x + (row % 2) * (bw // 2)) // bw + row * rng.randrange(1, NUM_COLOURS)


def pat_diamonds(w, h, rng):
    x, y = _grid(w, h)
    cx, cy = rng.randrange(w), rng.randrange(h)
    return (np.abs(x - cx) + np.abs(y - cy)) // rng.randrange(8, 50)


def pat_triangles(w, h, rng):
    x, y = _grid(w, h)
    size = rng.randrange(12, 80)
    upper = (x % size + y % size) >= size
    return (x // size) * 2 + upper + (y // size) * rng.randrange(1, NUM_COLOURS)


_BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]])


def pat_dither(w, h, rng):
    """Colour ramp whose bands blend into each other with pixel-level dithering."""
    x, y = _grid(w, h)
    pos = (x, y, x + y)[rng.randrange(3)]
    span = rng.randrange(40, 200)  # pixels per colour band
    return (pos * 16 + _BAYER[y % 4, x % 4] * span) // (span * 16)


PATTERNS = [
    ("vertical stripes", pat_vstripes),
    ("horizontal stripes", pat_hstripes),
    ("colour bars", pat_colour_bars),
    ("diagonal stripes", pat_diagonal),
    ("checkerboard", pat_checker),
    ("rings", pat_rings),
    ("wedges", pat_wedges),
    ("random tiles", pat_tiles),
    ("plasma", pat_plasma),
    ("waves", pat_waves),
    ("quilt", pat_quilt),
    ("pinstripes", pat_pinstripes),
    ("noise", pat_noise),
    ("random rectangles", pat_rectangles),
    ("voronoi cells", pat_voronoi),
    ("spiral", pat_spiral),
    ("dots", pat_dots),
    ("bricks", pat_bricks),
    ("diamonds", pat_diamonds),
    ("triangles", pat_triangles),
    ("dithered ramp", pat_dither),
]


def _shuffled(n, rng):
    order = list(range(n))
    for i in range(n - 1, 0, -1):
        j = rng.randrange(i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def frame_for_slot(slot, w, h, seed=0):
    """Return (description, (h, w) uint8 index array, border colour) for a slot."""
    block, phase = divmod(slot, BLOCK_LEN)
    if phase == NUM_COLOURS:
        return "CLEAN", np.full((h, w), CLEAN, dtype=np.uint8), WHITE

    # The colour order for this block: start at a random colour and step round
    # the 7 by 1..6 each frame. Any step still gives every pixel all 7 colours
    # once; using each step once per 6 blocks (in shuffled order) means every
    # colour-to-colour transition gets exercised equally often.
    group, idx = divmod(block, NUM_COLOURS - 1)
    step = 1 + _shuffled(NUM_COLOURS - 1, random.Random(seed * 15485863 + group * 7 + 3))[idx]
    start = random.Random(seed * 32452843 + block * 11 + 5).randrange(NUM_COLOURS)
    colours = ((np.arange(NUM_COLOURS) * step + start) % NUM_COLOURS).astype(np.uint8)

    if block % SOLID_EVERY == SOLID_EVERY - 1:
        colour = int(colours[phase])
        return "solid " + COLOUR_NAMES[colour], np.full((h, w), colour, dtype=np.uint8), colour

    # Walk the pattern list in a freshly shuffled order each time round, so
    # every pattern comes up equally often but never in a fixed sequence.
    lap, pos = divmod(block - block // SOLID_EVERY, len(PATTERNS))
    order = _shuffled(len(PATTERNS), random.Random(seed * 1000003 + lap))
    name, func = PATTERNS[order[pos]]

    rng = random.Random(seed * 7919 + block * 104729 + 1)
    base = np.asarray(func(w, h, rng), dtype=np.int64)
    frame = colours[(base + phase) % NUM_COLOURS]
    name = "%s (rotation %d/%d)" % (name, phase + 1, NUM_COLOURS)
    return name, frame, int(colours[phase])


# --------------------------------------------------------------------------
# Display handling
# --------------------------------------------------------------------------

def to_image(frame):
    """Palette-mode image: Inky uses the indices as-is, with no dithering."""
    img = Image.fromarray(frame, "P")
    flat = [c for rgb in PREVIEW_RGB for c in rgb]
    img.putpalette(flat + [0, 0, 0] * (256 - len(PREVIEW_RGB)))
    return img


def open_display():
    try:
        import inky  # noqa: F401
    except ImportError:
        sys.exit("The 'inky' library isn't installed for %s.\n"
                 "Run this script with the InkFrame virtual environment instead:\n"
                 "    ~/inky-env/bin/python %s" % (sys.executable, " ".join(sys.argv)))
    try:
        from inky.auto import auto
        return auto()
    except Exception as exc:  # no EEPROM detected etc. - assume the 5.7" Impression
        print("inky auto-detect failed (%s); falling back to UC8159 600x448" % exc)
        from inky.inky_uc8159 import Inky
        return Inky()


def push(display, frame, border):
    display.set_border(border)
    display.set_image(to_image(frame))
    display.show()


def log(msg):
    print("%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stdout.flush()


def run(args):
    display = open_display()
    w, h = display.width, display.height
    end = time.time() + args.hours * 3600
    log("Panel %dx%d, refresh every %ds for %.1fh (until %s), seed %d"
        % (w, h, args.interval, args.hours,
           datetime.fromtimestamp(end).strftime("%Y-%m-%d %H:%M"), args.seed))

    stop = {"flag": False}

    def request_stop(signum, _frame):
        log("Stop requested - finishing up after the current refresh")
        stop["flag"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    last_slot, shown, failed = None, 0, 0
    while not stop["flag"] and time.time() < end:
        slot = int(time.time() // args.interval)
        if slot == last_slot:
            # Sleep in short hops so Ctrl-C is handled promptly.
            time.sleep(min(1.0, max(0.05, (slot + 1) * args.interval - time.time())))
            continue
        last_slot = slot
        name, frame, border = frame_for_slot(slot, w, h, args.seed)
        started = time.time()
        try:
            push(display, frame, border)
            shown += 1
            log("slot %d  %-34s  %.0fs" % (slot, name, time.time() - started))
        except Exception as exc:
            failed += 1
            log("slot %d  %s  FAILED: %r" % (slot, name, exc))

    # Leave the panel in a neutral state rather than on a test pattern.
    log("Finishing: CLEAN pass then white")
    for value in (CLEAN, WHITE):
        try:
            push(display, np.full((h, w), value, dtype=np.uint8), WHITE)
        except Exception as exc:
            log("final %s frame FAILED: %r" % (COLOUR_NAMES[value], exc))
    log("Done: %d refreshes shown, %d failed" % (shown, failed))


def preview(args):
    os.makedirs(args.preview, exist_ok=True)
    start = int(time.time() // args.interval) if args.start_slot is None else args.start_slot
    for slot in range(start, start + args.count):
        name, frame, _ = frame_for_slot(slot, args.width, args.height, args.seed)
        path = os.path.join(args.preview, "slot_%d.png" % slot)
        to_image(frame).convert("RGB").save(path)
        print("%s  %s" % (path, name))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--hours", type=float, default=24.0, help="how long to run (default 24)")
    p.add_argument("--interval", type=int, default=120,
                   help="seconds between refreshes; must match on all units (default 120)")
    p.add_argument("--seed", type=int, default=0,
                   help="pattern seed; must match on all units (default 0)")
    p.add_argument("--preview", metavar="DIR",
                   help="write PNG previews to DIR instead of driving a panel")
    p.add_argument("--count", type=int, default=24, help="number of preview frames (default 24)")
    p.add_argument("--start-slot", type=int, default=None, help="first preview slot (default: now)")
    p.add_argument("--width", type=int, default=600, help="preview width")
    p.add_argument("--height", type=int, default=448, help="preview height")
    args = p.parse_args()
    if args.interval < 45:
        p.error("--interval must be at least 45s; a 7-colour refresh takes ~30-40s")
    if args.preview:
        preview(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
