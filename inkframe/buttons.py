"""Read the four side buttons with libgpiod (RPi.GPIO no longer works on current Pi OS)."""
import logging
import threading
import time

log = logging.getLogger(__name__)

# BCM pin numbers on the Inky Impression 5.7"
PINS = {"A": 5, "B": 6, "C": 16, "D": 24}
DEBOUNCE = 0.3     # ignore repeat presses of the same button closer together than this
MIN_PRESS = 0.03   # contact bounce shorter than this is ignored
HOLD = 1.5         # a press at least this long is reported as "<label>_hold"


def start(callback):
    """Call callback("A".."D", or "A_hold".."D_hold") from a background thread on release."""
    thread = threading.Thread(target=_run, args=(callback,), daemon=True, name="buttons")
    thread.start()
    return thread


def _run(callback):
    try:
        import gpiod
        import gpiodevice
        from gpiod.line import Bias, Direction, Edge

        settings = gpiod.LineSettings(
            direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=Edge.BOTH
        )
        chip = gpiodevice.find_chip_by_platform()
        offsets = []
        for pin in PINS.values():
            try:
                offsets.append(chip.line_offset_from_id(pin))
            except Exception:
                offsets.append(pin)
        labels = dict(zip(offsets, PINS))
        request = chip.request_lines(
            consumer="inkframe-buttons", config=dict.fromkeys(offsets, settings)
        )
    except Exception:
        log.exception("buttons unavailable; continuing without them")
        return

    log.info("listening for buttons on GPIO %s", list(PINS.values()))
    falling = gpiod.EdgeEvent.Type.FALLING_EDGE
    pressed, last = {}, {}
    while True:
        try:
            for event in request.read_edge_events():
                label = labels.get(event.line_offset)
                if label is None:
                    continue
                ts = event.timestamp_ns / 1e9
                if event.event_type == falling:        # button down (pins are pulled up)
                    start = pressed.get(label)
                    if start is None or ts - start >= MIN_PRESS:
                        pressed[label] = ts
                    continue
                start = pressed.get(label)
                if start is None or ts - start < MIN_PRESS:
                    continue                            # bounce while pressing
                del pressed[label]
                held = ts - start
                if ts - last.get(label, float("-inf")) < DEBOUNCE:
                    continue
                last[label] = ts
                if held >= HOLD:
                    log.info("button %s held (%.1fs)", label, held)
                    callback(label + "_hold")
                else:
                    log.info("button %s pressed", label)
                    callback(label)
        except Exception:
            log.exception("button read failed; retrying")
            time.sleep(1)
