"""Wikimedia Commons Picture of the Day (no API key needed).

The photo is downloaded once a day and cached, so the network is only touched
when the date changes or the button is pressed while already in this mode.
"""
import html
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import date, timedelta

from PIL import Image, ImageDraw

from ..display import BLACK, WHITE, new_canvas
from ..fonts import font
from ..photo import dither, prepare

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "InkFrame/1.0 (Raspberry Pi e-ink photo frame)"}
CAPTION_HEIGHT = 52


class PotdMode:
    name = "potd"

    def __init__(self, cfg, state_dir):
        self.cfg = cfg["potd"]
        self.saturation = float(cfg["display"]["saturation"])
        self.interval = self.cfg["interval_minutes"] * 60
        self.dir = state_dir / "potd"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_file = self.dir / "meta.json"
        self.image_file = self.dir / "image.jpg"
        self.shown = None

    def next(self):
        pass

    def changed(self):
        """Timed refreshes only redraw when a new day's picture has arrived."""
        try:
            return self._current()["date"] != self.shown
        except Exception:
            return True

    # ---------- data ----------
    def _api(self, **params):
        params.update(format="json", formatversion=2)
        req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def _expand(self, template):
        return self._api(action="expandtemplates", prop="wikitext",
                         text="{{" + template + "}}")["expandtemplates"]["wikitext"].strip()

    def _fetch(self, day):
        filename = self._expand(f"Potd/{day.isoformat()}")
        if not filename or "Potd/" in filename:
            raise LookupError(f"no picture of the day for {day}")
        width = 1200
        info = self._api(action="query", titles=f"File:{filename}", prop="imageinfo",
                         iiprop="url|extmetadata", iiurlwidth=width)
        page = info["query"]["pages"][0]["imageinfo"][0]
        meta_ext = page.get("extmetadata", {})
        try:
            caption = _plain(self._expand(f"Potd/{day.isoformat()} ({self.cfg['language']})"))
        except Exception:
            caption = ""
        if not caption or "Potd/" in caption:
            caption = _plain(meta_ext.get("ObjectName", {}).get("value", "")) or filename
        artist = _plain(meta_ext.get("Artist", {}).get("value", ""))

        req = urllib.request.Request(page.get("thumburl") or page["url"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            tmp = self.image_file.with_suffix(".part")
            tmp.write_bytes(resp.read())
        tmp.replace(self.image_file)
        meta = {"date": day.isoformat(), "file": filename, "caption": caption, "artist": artist}
        self.meta_file.write_text(json.dumps(meta))
        log.info("picture of the day for %s: %s", day, filename)
        return meta

    def _current(self):
        meta = json.loads(self.meta_file.read_text()) if self.meta_file.exists() else None
        today = date.today()
        if meta and meta["date"] == today.isoformat() and self.image_file.exists():
            return meta
        try:
            try:
                return self._fetch(today)
            except LookupError:
                return self._fetch(today - timedelta(days=1))
        except Exception as exc:
            if meta and self.image_file.exists():
                log.warning("picture of the day fetch failed (%s); showing the last one", exc)
                return meta
            raise

    # ---------- drawing ----------
    def render(self, size):
        meta = self._current()
        self.shown = meta["date"]
        show_caption = self.cfg["caption"]
        w, h = size
        photo_h = h - CAPTION_HEIGHT if show_caption else h
        with Image.open(self.image_file) as im:
            photo = prepare(im, (w, photo_h), self.cfg["fit"], self.cfg["colour_boost"],
                            self.cfg["contrast_boost"])
        if not show_caption:
            return photo

        # Dither the photo ourselves so the caption text stays crisp, not dithered.
        img = new_canvas(size)
        img.paste(dither(photo, self.saturation), (0, 0))
        d = ImageDraw.Draw(img)
        d.line((0, photo_h, w, photo_h), fill=BLACK, width=2)
        d.text((10, photo_h + 6), _fit(d, meta["caption"], font(16, True), w - 20),
               font=font(16, True), fill=BLACK)
        credit = "Wikimedia Commons Picture of the Day"
        if meta.get("artist"):
            credit += " · " + meta["artist"]
        d.text((10, photo_h + 30), _fit(d, credit, font(13), w - 20), font=font(13), fill=BLACK)
        return img


def _plain(text):
    """Strip wikitext / HTML down to readable text."""
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)   # [[link|label]]
    text = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", text)         # [url label]
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)                       # leftover templates
    text = re.sub(r"<[^>]+>", "", text)                              # html tags
    text = html.unescape(text.replace("'''", "").replace("''", ""))
    return re.sub(r"\s+", " ", text).strip()


def _fit(d, text, fnt, max_width):
    """Shorten text with an ellipsis so it fits on one line."""
    if d.textlength(text, font=fnt) <= max_width:
        return text
    while text and d.textlength(text + "…", font=fnt) > max_width:
        text = text[:-1]
    return text.rstrip(" ,.;:") + "…"
