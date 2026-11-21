"""Thin wrapper around the Inky driver, plus a dry-run mode that saves PNG previews.

Modes draw on canvases whose pixel values are InkFrame's own colour indices
(BLACK, WHITE, ... below). Each supported panel says which of those it can show
and how they map onto the driver's native values, so the same drawing code works
on the 7-colour 5.7" and the 6-colour Spectra 7.3" panels.
"""
import logging
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

# The driver warns on every refresh while it waits for the panel; it's expected, not a fault.
warnings.filterwarnings("ignore", message="Busy Wait", category=UserWarning)

# InkFrame colour indices used on canvases (the same order as the 7-colour panels).
BLACK, WHITE, GREEN, BLUE, RED, YELLOW, ORANGE, CLEAN = range(8)

# The "ideal" colours the driver blends toward at saturation 0.
PURE_RGB = [
    (0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
    (255, 0, 0), (255, 255, 0), (255, 140, 0), (255, 255, 255),
]


@dataclass(frozen=True)
class Panel:
    name: str
    description: str
    size: tuple
    colours: int        # InkFrame indices 0..colours-1 are real panel colours
    native: tuple       # InkFrame index (0..7) -> value the driver sends to the panel
    rgb: tuple          # InkFrame index -> roughly what the panel looks like (previews, dithering)
    has_clean: bool     # the panel has a dedicated clean waveform


PANELS = {
    "impression57": Panel(
        "impression57", 'Inky Impression 5.7" (7-colour)', (600, 448), 7,
        native=tuple(range(8)),
        rgb=((57, 48, 57), (255, 255, 255), (58, 91, 70), (61, 59, 94),
             (156, 72, 75), (208, 190, 71), (177, 106, 73), (255, 255, 255)),
        has_clean=True),
    "impression73-7colour": Panel(
        "impression73-7colour", 'Inky Impression 7.3" (7-colour, 2023)', (800, 480), 7,
        native=tuple(range(8)),
        rgb=((57, 48, 57), (255, 255, 255), (58, 91, 70), (61, 59, 94),
             (156, 72, 75), (208, 190, 71), (177, 106, 73), (255, 255, 255)),
        has_clean=True),
    # Spectra 6: black, white, yellow, red, blue, green. No orange, so ORANGE is
    # drawn red; no clean waveform, so CLEAN is drawn white.
    "spectra73": Panel(
        "spectra73", 'Inky Impression 7.3" (Spectra 6, 2025)', (800, 480), 6,
        #       BLACK WHITE GREEN BLUE RED YELLOW ORANGE->red CLEAN->white
        native=(0, 1, 6, 5, 3, 2, 3, 1),
        rgb=((0, 0, 0), (161, 164, 165), (58, 91, 70), (61, 59, 94),
             (156, 72, 75), (208, 190, 71), (156, 72, 75), (161, 164, 165)),
        has_clean=False),
}

# Which panel profile goes with which Inky driver module.
DRIVER_PANELS = {
    "inky.inky_uc8159": "impression57",
    "inky.inky_ac073tc1a": "impression73-7colour",
    "inky.inky_e673": "spectra73",
}

DEFAULT_DRY_RUN_PANEL = "spectra73"
PANEL = PANELS[DEFAULT_DRY_RUN_PANEL]


def set_panel(name):
    global PANEL
    PANEL = PANELS[name]
    return PANEL


def _flat(colours):
    flat = [c for rgb in colours for c in rgb]
    return flat + [0, 0, 0] * (256 - len(colours))


def preview_rgb(panel=None):
    """What each InkFrame index looks like on screen, for preview PNGs.

    The Spectra driver models white as grey for dithering; previews show it white.
    """
    panel = panel or PANEL
    return [(255, 255, 255) if panel.native[i] == panel.native[WHITE] else rgb
            for i, rgb in enumerate(panel.rgb)]


def new_canvas(size=None, fill=WHITE):
    """A palette image whose pixel values are InkFrame colour indices."""
    img = Image.new("P", size or PANEL.size, fill)
    img.putpalette(_flat(preview_rgb()))
    return img


def _driver(name):
    if name == "impression57":
        from inky.inky_uc8159 import Inky
        return Inky(resolution=(600, 448))
    if name == "impression73-7colour":
        from inky.inky_ac073tc1a import Inky
        return Inky(resolution=(800, 480))
    from inky.inky_e673 import Inky
    return Inky(resolution=(800, 480))


class Display:
    def __init__(self, cfg, dry_run=False, preview_dir="preview"):
        self.saturation = float(cfg["display"]["saturation"])
        self.rotate = int(cfg["display"]["rotate"])
        self.dry_run = dry_run
        self.preview_dir = Path(preview_dir)
        wanted = cfg["display"].get("panel", "auto")
        if wanted != "auto" and wanted not in PANELS:
            raise ValueError(f"unknown [display] panel {wanted!r}; use 'auto' or one of "
                             + ", ".join(PANELS))
        if dry_run:
            self.inky = None
            set_panel(DEFAULT_DRY_RUN_PANEL if wanted == "auto" else wanted)
            self.preview_dir.mkdir(parents=True, exist_ok=True)
        elif wanted == "auto":
            from inky.auto import auto
            self.inky = auto(ask_user=False)
            if self.inky is None:
                raise RuntimeError(
                    "couldn't identify the Inky board from its EEPROM. Check that I2C and SPI "
                    "are enabled, or set panel under [display] in config.toml")
            module = type(self.inky).__module__
            if module not in DRIVER_PANELS:
                raise RuntimeError(f"InkFrame doesn't support this Inky board yet ({module})")
            set_panel(DRIVER_PANELS[module])
        else:
            set_panel(wanted)
            self.inky = _driver(wanted)
        self.panel = PANEL
        self.size = PANEL.size if dry_run else tuple(self.inky.resolution)
        log.info("display ready: %s, %sx%s%s", PANEL.description, *self.size,
                 " (dry run)" if dry_run else "")

    def show(self, img, label="frame"):
        """Push an image to the panel. 'P' images are drawn with panel indices; RGB is dithered."""
        if self.rotate:
            img = img.rotate(self.rotate)
        start = time.monotonic()
        if self.dry_run:
            self._save_preview(img, label)
        else:
            if img.mode == "P":
                self._set_indexed(img)
            else:
                self.inky.set_image(img.convert("RGB"), saturation=self.saturation)
            self.inky.show()
        log.info("refreshed %s in %.1fs", label, time.monotonic() - start)

    def _set_indexed(self, img):
        """Send InkFrame colour indices straight to the panel, with no re-dithering."""
        import numpy
        lut = numpy.array(self.panel.native + (self.panel.native[WHITE],) * 248, dtype=numpy.uint8)
        idx = numpy.asarray(img, dtype=numpy.uint8)
        self.inky.buf = lut[idx].reshape((self.inky.height, self.inky.width))

    def clean(self):
        """Reduce ghosting: the clean waveform where the panel has one, else black then white."""
        log.info("running clean cycle")
        if self.panel.has_clean:
            self.show(new_canvas(self.size, CLEAN), "clean")
        else:
            self.show(new_canvas(self.size, BLACK), "clean-black")
            self.show(new_canvas(self.size, WHITE), "clean-white")

    def _save_preview(self, img, label):
        if img.mode != "P":
            from .photo import dither
            img = dither(img, self.saturation)
        img = img.copy()
        img.putpalette(_flat(preview_rgb(self.panel)))
        out = self.preview_dir / f"{time.strftime('%H%M%S')}-{label}.png"
        img.convert("RGB").save(out)
        log.info("preview saved to %s", out)
