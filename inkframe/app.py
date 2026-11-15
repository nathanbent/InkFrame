"""Main loop: refresh the current mode on a timer and react to the side buttons.

Buttons:
  A  weather dashboard
  B  photo slideshow
  C  Wikimedia Commons picture of the day
  D  calendar
  Hold D for 2 seconds to clean the screen (reduces ghosting), then redraw.

Pressing the button for the mode already showing refreshes it
(next photo in the slideshow, re-fetch the weather or picture of the day).
"""
import argparse
import json
import logging
import logging.handlers
import queue
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import ImageDraw

from . import buttons, config
from .display import BLACK, RED, Display, new_canvas
from .fonts import font
from .modes import MODES

log = logging.getLogger("inkframe")
BUTTON_MODES = {"A": "weather", "B": "slideshow", "C": "potd", "D": "calendar"}
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


class App:
    def __init__(self, cfg, display):
        self.cfg = cfg
        self.display = display
        self.state_file = cfg["app"]["state_dir"] / "state.json"
        self.modes = {name: cls(cfg, cfg["app"]["state_dir"]) for name, cls in MODES.items()}
        self.mode = self._saved_mode() or cfg["app"]["start_mode"]
        if self.mode not in self.modes:
            self.mode = "weather"
        self.presses = queue.Queue()
        self.since_clean = 0

    # ---------- state ----------
    def _saved_mode(self):
        try:
            return json.loads(self.state_file.read_text())["mode"]
        except Exception:
            return None

    def _set_mode(self, mode):
        self.mode = mode
        self.state_file.write_text(json.dumps({"mode": mode}))

    # ---------- actions ----------
    def handle_button(self, label):
        """Turn a button label into (clean_first, advance)."""
        if label == "D_hold":
            return True, False
        mode = BUTTON_MODES.get(label.removesuffix("_hold"))
        if mode is None:
            return None
        advance = self.mode == mode
        self._set_mode(mode)
        return False, advance

    def refresh(self, clean_first=False, advance=True, timed=False):
        mode = self.modes[self.mode]
        if timed and not getattr(mode, "changed", lambda: True)():
            log.info("%s unchanged; skipping refresh", self.mode)
            return
        clean_every = self.cfg["display"]["clean_every"]
        if clean_first or (clean_every and self.since_clean >= clean_every):
            self.display.clean()
            self.since_clean = 0
        try:
            if advance:
                mode.next()
            img = mode.render(self.display.size)
        except Exception as exc:
            detail = " ".join(str(exc).split())
            if len(detail) > 400:
                detail = detail[:400] + "..."
            log.error("%s failed to render: %s", self.mode, detail[:1000])
            log.debug("traceback", exc_info=True)
            img = self._error_screen(f"{self.mode} failed", detail)
        try:
            self.display.show(img, self.mode)
        except Exception:
            log.exception("display refresh failed")
        self.since_clean += 1

    def _error_screen(self, title, detail):
        img = new_canvas(self.display.size)
        d = ImageDraw.Draw(img)
        d.text((20, 20), title, font=font(28, True), fill=RED)
        d.multiline_text((20, 70), _wrap(detail, 50), font=font(16), fill=BLACK)
        d.text((20, self.display.size[1] - 30), "Will retry automatically. Log: journalctl -u inkframe",
               font=font(14), fill=BLACK)
        return img

    def _quiet(self):
        hours = self.cfg["display"]["quiet_hours"]
        if len(hours) != 2:
            return False
        start, end = hours
        h = datetime.now().hour
        return start <= h < end if start < end else (h >= start or h < end)

    # ---------- loop ----------
    def run(self, first=(False, True)):
        log.info("starting in %s mode", self.mode)
        action = first
        next_due = 0.0
        while True:
            if action is None:
                try:
                    action = self.handle_button(
                        self.presses.get(timeout=max(0.0, next_due - time.monotonic())))
                except queue.Empty:
                    if self._quiet():
                        next_due = time.monotonic() + 300
                        continue
                    action = (False, True, True)
                if action is None:
                    continue
            self.refresh(*action)
            next_due = time.monotonic() + self.modes[self.mode].interval
            # Presses made during the ~30 s refresh: act on the most recent one only.
            action, latest = None, None
            while not self.presses.empty():
                latest = self.presses.get_nowait()
            if latest:
                action = self.handle_button(latest)


def _wrap(text, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return "\n".join(lines + [line])


def main(argv=None):
    p = argparse.ArgumentParser(prog="inkframe", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=DEFAULT_CONFIG, help="path to config.toml")
    p.add_argument("--mode", choices=sorted(MODES), help="start in this mode")
    p.add_argument("--once", action="store_true", help="draw one frame and exit")
    p.add_argument("--clean", action="store_true", help="clean the screen before drawing")
    p.add_argument("--dry-run", action="store_true",
                   help="don't touch the display; save PNGs to ./preview instead")
    args = p.parse_args(argv)

    cfg = config.load(args.config)
    handlers = [logging.StreamHandler(sys.stderr),
                logging.handlers.RotatingFileHandler(
                    cfg["app"]["state_dir"] / "inkframe.log", maxBytes=1_000_000, backupCount=2)]
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = App(cfg, Display(cfg, dry_run=args.dry_run))
    if args.mode:
        app._set_mode(args.mode)
    if not args.dry_run and not args.once and cfg["buttons"]["enabled"]:
        buttons.start(app.presses.put)
    if args.once:
        app.refresh(clean_first=args.clean)
        return
    try:
        app.run(first=(args.clean, True))
    except KeyboardInterrupt:
        log.info("stopped")
