"""Thin wrapper around the Inky driver, plus a dry-run mode that saves PNG previews."""
import logging
import time
import warnings
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

# The driver warns on every refresh while it waits for the panel; it's expected, not a fault.
warnings.filterwarnings("ignore", message="Busy Wait", category=UserWarning)

# Palette indices used by the UC8159 7-colour panel.
BLACK, WHITE, GREEN, BLUE, RED, YELLOW, ORANGE, CLEAN = range(8)

# Roughly what the panel actually looks like; used for previews and for drawing.
PANEL_RGB = [
    (57, 48, 57), (255, 255, 255), (58, 91, 70), (61, 59, 94),
    (156, 72, 75), (208, 190, 71), (177, 106, 73), (255, 255, 255),
]
# The "ideal" colours the driver blends toward at saturation 0.
PURE_RGB = [
    (0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
    (255, 0, 0), (255, 255, 0), (255, 140, 0), (255, 255, 255),
]

SIZE = (600, 448)


def _flat(colours):
    flat = [c for rgb in colours for c in rgb]
    return flat + [0, 0, 0] * (256 - len(colours))


def new_canvas(size=SIZE, fill=WHITE):
    """A palette image whose pixel values are panel colour indices."""
    img = Image.new("P", size, fill)
    img.putpalette(_flat(PANEL_RGB))
    return img


class Display:
    def __init__(self, cfg, dry_run=False, preview_dir="preview"):
        self.saturation = float(cfg["display"]["saturation"])
        self.rotate = int(cfg["display"]["rotate"])
        self.dry_run = dry_run
        self.preview_dir = Path(preview_dir)
        if dry_run:
            self.inky = None
            self.size = SIZE
            self.preview_dir.mkdir(parents=True, exist_ok=True)
        else:
            from inky.auto import auto
            self.inky = auto()
            self.size = tuple(self.inky.resolution)
        log.info("display ready: %sx%s%s", *self.size, " (dry run)" if dry_run else "")

    def show(self, img, label="frame"):
        """Push an image to the panel. 'P' images are drawn with panel indices; RGB is dithered."""
        if self.rotate:
            img = img.rotate(self.rotate)
        start = time.monotonic()
        if self.dry_run:
            self._save_preview(img, label)
        else:
            if img.mode == "P":
                self.inky.set_image(img)
            else:
                self.inky.set_image(img.convert("RGB"), saturation=self.saturation)
            self.inky.show()
        log.info("refreshed %s in %.1fs", label, time.monotonic() - start)

    def clean(self):
        """Drive every pixel through the panel's clean waveform to reduce ghosting."""
        log.info("running clean cycle")
        self.show(new_canvas(self.size, CLEAN), "clean")

    def _save_preview(self, img, label):
        if img.mode != "P":
            from .photo import dither
            img = dither(img, self.saturation)
        out = self.preview_dir / f"{time.strftime('%H%M%S')}-{label}.png"
        img.convert("RGB").save(out)
        log.info("preview saved to %s", out)
