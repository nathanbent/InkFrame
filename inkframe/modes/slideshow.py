"""Photo slideshow from a local folder.

The photo root is [slideshow] folder in config.toml. The web page can narrow the
slideshow to one folder under the root, change the order, interval and fit; those
choices are saved in the state directory and override config.toml.
"""
import json
import logging
import random
from pathlib import Path

from PIL import Image, ImageDraw

from ..display import BLACK, new_canvas
from ..fonts import font
from ..photo import prepare

log = logging.getLogger(__name__)

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
SETTINGS = ("folder", "subfolders", "order", "interval_minutes", "fit")


def is_photo(path):
    return path.suffix.lower() in EXTENSIONS and not path.name.startswith(".")


class SlideshowMode:
    name = "slideshow"

    def __init__(self, cfg, state_dir):
        self.cfg = cfg["slideshow"]
        self.root = self.cfg["folder"]
        self.state = state_dir / "slideshow.json"
        self.settings_file = state_dir / "slideshow_settings.json"
        self.settings = {"folder": "", "subfolders": True, "order": "shuffle",
                         "interval_minutes": self.cfg["interval_minutes"], "fit": self.cfg["fit"]}
        try:
            saved = json.loads(self.settings_file.read_text())
            self.settings.update({k: v for k, v in saved.items() if k in SETTINGS})
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("ignoring unreadable %s", self.settings_file)
        self.queue = []
        self.current = None
        if self.state.exists():
            try:
                saved = json.loads(self.state.read_text())
                self.queue, self.current = saved["queue"], saved.get("current")
            except Exception:
                pass

    @property
    def interval(self):
        return max(1, float(self.settings["interval_minutes"])) * 60

    @property
    def folder(self):
        """The folder the slideshow plays from (the root, or one folder under it)."""
        folder = (self.root / self.settings["folder"]).resolve()
        root = self.root.resolve()
        return folder if folder == root or root in folder.parents else root

    def configure(self, **changes):
        """Change slideshow settings (from the web page) and start over from the new set."""
        changes = {k: v for k, v in changes.items() if k in SETTINGS}
        if "order" in changes and changes["order"] not in ("shuffle", "name"):
            raise ValueError("order must be 'shuffle' or 'name'")
        if "fit" in changes and changes["fit"] not in ("crop", "fit", "auto"):
            raise ValueError("fit must be 'crop', 'fit' or 'auto'")
        if "interval_minutes" in changes:
            changes["interval_minutes"] = max(1.0, float(changes["interval_minutes"]))
        if "subfolders" in changes:
            changes["subfolders"] = bool(changes["subfolders"])
        if "folder" in changes:
            changes["folder"] = str(changes["folder"]).strip("/")
        self.settings.update(changes)
        self.settings_file.write_text(json.dumps(self.settings))
        self.queue = []
        self._save()
        log.info("slideshow settings: %s", self.settings)

    def photos(self):
        folder = self.folder
        if not folder.is_dir():
            return []
        found = folder.rglob("*") if self.settings["subfolders"] else folder.iterdir()
        return sorted((str(p) for p in found if is_photo(p) and p.is_file()),
                      key=lambda p: p.lower())

    def next(self):
        """Advance to the next photo; the following render() shows it."""
        photos = self.photos()
        if self.settings["order"] == "name":
            self.queue = []
            if self.current in photos:
                self.current = photos[(photos.index(self.current) + 1) % len(photos)]
            else:
                # Carry on from where the current photo would sort, or start at the beginning.
                later = [p for p in photos if self.current and p.lower() > self.current.lower()]
                self.current = (later or photos or [None])[0]
        else:
            present = set(photos)
            self.queue = [p for p in self.queue if p in present]
            if not self.queue:
                self.queue = random.sample(photos, len(photos))
                if len(self.queue) > 1 and self.queue[0] == self.current:
                    self.queue.append(self.queue.pop(0))
            self.current = self.queue.pop(0) if self.queue else None
        self._save()

    def show(self, path):
        """Make a particular photo the current one (the web page's "show now")."""
        self.current = str(path)
        if self.current in self.queue:
            self.queue.remove(self.current)
        self._save()

    def _save(self):
        self.state.write_text(json.dumps({"queue": self.queue, "current": self.current}))

    def render(self, size):
        for _ in range(5):
            if self.current is None or not Path(self.current).is_file():
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
            return prepare(im, size, self.settings["fit"], self.cfg["colour_boost"],
                           self.cfg["contrast_boost"])

    def _empty(self, size):
        img = new_canvas(size)
        d = ImageDraw.Draw(img)
        d.text((size[0] // 2, size[1] // 2 - 20), "No photos yet", font=font(32, True),
               fill=BLACK, anchor="mm")
        d.text((size[0] // 2, size[1] // 2 + 25), f"Copy some into {self.folder}",
               font=font(18), fill=BLACK, anchor="mm")
        return img
