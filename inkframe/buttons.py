"""Read the four side buttons with libgpiod (RPi.GPIO no longer works on current Pi OS)."""
import logging
import threading
import time

log = logging.getLogger(__name__)

# BCM pin numbers on the Inky Impression 5.7"
PINS = {"A": 5, "B": 6, "C": 16, "D": 24}
DEBOUNCE = 0.3


def start(callback):
    """Call callback("A".."D") from a background thread whenever a button is pressed."""
    thread = threading.Thread(target=_run, args=(callback,), daemon=True, name="buttons")
    thread.start()
    return thread


def _run(callback):
    try:
        import gpiod
        import gpiodevice
        from gpiod.line import Bias, Direction, Edge

        settings = gpiod.LineSettings(
            direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=Edge.FALLING
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
    last = {}
    while True:
        try:
            for event in request.read_edge_events():
                label = labels.get(event.line_offset)
                now = time.monotonic()
                if label is None or now - last.get(label, 0) < DEBOUNCE:
                    continue
                last[label] = now
                log.info("button %s pressed", label)
                callback(label)
        except Exception:
            log.exception("button read failed; retrying")
            time.sleep(1)
