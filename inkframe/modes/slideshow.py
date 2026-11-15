"""Photo slideshow from a local folder (shuffled, no repeats until every photo has shown)."""
import json
import logging
import random

from PIL import Image, ImageDraw

from ..display import BLACK, WHITE, new_canvas
from ..fonts import font
from ..photo import prepare

log = logging.getLogger(__name__)

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


class SlideshowMode:
    name = "slideshow"

    def __init__(self, cfg, state_dir):
        self.cfg = cfg["slideshow"]
        self.folder = self.cfg["folder"]
        self.interval = self.cfg["interval_minutes"] * 60
        self.state = state_dir / "slideshow.json"
        self.queue = []
        self.current = None
        if self.state.exists():
            try:
                saved = json.loads(self.state.read_text())
                self.queue, self.current = saved["queue"], saved.get("current")
            except Exception:
                pass

    def _photos(self):
        if not self.folder.is_dir():
            return []
        return sorted(str(p) for p in self.folder.rglob("*")
                      if p.suffix.lower() in EXTENSIONS and p.is_file())

    def next(self):
        """Advance to the next photo; the following render() shows it."""
        photos = self._photos()
        self.queue = [p for p in self.queue if p in set(photos)]
        if not self.queue:
            self.queue = random.sample(photos, len(photos))
            if len(self.queue) > 1 and self.queue[0] == self.current:
                self.queue.append(self.queue.pop(0))
        self.current = self.queue.pop(0) if self.queue else None
        self.state.write_text(json.dumps({"queue": self.queue, "current": self.current}))

    def render(self, size):
        for _ in range(5):
            if self.current is None:
                self.next()
            if self.current is None:
                return self._empty(size)
            try:
                return self._load(self.current, size)
            except Exception:
                log.exception("could not open %s; skipping", self.current)
                self.current = None
        return self._empty(size)

    def _load(self, path, size):
        log.info("showing %s", path)
        with Image.open(path) as im:
            return prepare(im, size, self.cfg["fit"], self.cfg["colour_boost"],
                           self.cfg["contrast_boost"])

    def _empty(self, size):
        img = new_canvas(size)
        d = ImageDraw.Draw(img)
        d.text((size[0] // 2, size[1] // 2 - 20), "No photos yet", font=font(32, True),
               fill=BLACK, anchor="mm")
        d.text((size[0] // 2, size[1] // 2 + 25), f"Copy some into {self.folder}",
               font=font(18), fill=BLACK, anchor="mm")
        return img
