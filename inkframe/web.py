"""A small web page for controlling the frame from a phone or computer on the same network.

Standard library only. Commands go into the app's queue, the same one the buttons
use, so the main loop stays the only thing that touches the display. File browsing,
uploads and thumbnails are handled here, confined to the slideshow's photo folder.
"""
import hashlib
import io
import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps

from .modes.slideshow import is_photo

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 60 * 1024 * 1024
THUMB_SIZE = 240
MODE_NAMES = {"weather": "Weather", "slideshow": "Slideshow", "potd": "Picture of the day",
              "calendar": "Calendar"}


class BadRequest(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def start(app, host="0.0.0.0", port=8080):
    try:
        server = ThreadingHTTPServer((host, port), _handler(app))
    except OSError as exc:
        log.error("web page unavailable: can't listen on %s:%s (%s)", host, port, exc)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    log.info("web page on http://%s:%s/", "<this-pi>" if host == "0.0.0.0" else host, port)
    return server


def _handler(app):
    slideshow = app.modes["slideshow"]
    thumbs = app.cfg["app"]["state_dir"] / "thumbs"
    thumbs.mkdir(exist_ok=True)
    thumb_slots = threading.Semaphore(2)     # decoding big JPEGs is slow and memory-hungry on a Zero
    screen_cache = {"version": -1, "png": None}
    screen_lock = threading.Lock()

    def root():
        return slideshow.root.resolve()

    def resolve(rel, must_exist=True):
        """A path under the photo folder, from a path relative to it; refuses anything outside."""
        rel = (rel or "").strip("/")
        if any(part.startswith(".") for part in Path(rel).parts):
            raise BadRequest("bad path")
        path = (root() / rel).resolve()
        if path != root() and root() not in path.parents:
            raise BadRequest("bad path")
        if must_exist and not path.exists():
            raise BadRequest("not found", HTTPStatus.NOT_FOUND)
        return path

    def relative(path):
        try:
            return str(Path(path).resolve().relative_to(root()))
        except (ValueError, OSError):
            return None

    def clean_name(name):
        name = (name or "").strip()
        if not name or "/" in name or "\\" in name or name.startswith(".") or len(name) > 200:
            raise BadRequest("bad name")
        return name

    def status():
        s = slideshow.settings
        current = relative(slideshow.current) if slideshow.current else None
        return {
            "mode": app.mode,
            "modes": [{"id": m, "name": MODE_NAMES.get(m, m)} for m in app.modes],
            "busy": app.busy,
            "last_refresh": app.last_refresh,
            "next_refresh": app.next_due_wall,
            "screen_version": app.screen_version,
            "size": list(app.display.size),
            "panel": app.display.panel.description,
            "photo": current,
            "photo_root": str(slideshow.root),
            "slideshow": {**s, "count": len(slideshow.photos())},
            "queued": app.presses.qsize(),
        }

    def folders():
        base = root()
        out = []
        if not base.is_dir():
            return out
        dirs = [base] + sorted((p for p in base.rglob("*") if p.is_dir()
                                and not any(x.startswith(".") for x in p.relative_to(base).parts)),
                               key=lambda p: str(p).lower())
        for d in dirs:
            here = sum(1 for p in d.iterdir() if is_photo(p) and p.is_file())
            out.append({"path": "" if d == base else str(d.relative_to(base)), "photos": here})
        # Photos including subfolders, for the slideshow picker.
        totals = {f["path"]: f["photos"] for f in out}
        for f in out:
            prefix = f["path"] + "/" if f["path"] else ""
            f["total"] = sum(n for p, n in totals.items() if p == f["path"] or p.startswith(prefix))
        return out

    def listing(rel):
        folder = resolve(rel)
        if not folder.is_dir():
            raise BadRequest("not a folder")
        entries = sorted((p for p in folder.iterdir() if not p.name.startswith(".")),
                         key=lambda p: p.name.lower())
        return {
            "folder": relative(folder),
            "folders": [p.name for p in entries if p.is_dir()],
            "photos": [{"name": p.name, "path": relative(p), "size": p.stat().st_size}
                       for p in entries if p.is_file() and is_photo(p)],
        }

    def thumbnail(rel):
        path = resolve(rel)
        if not path.is_file() or not is_photo(path):
            raise BadRequest("not a photo")
        st = path.stat()
        key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
        cached = thumbs / f"{key}.jpg"
        if not cached.exists():
            with thumb_slots:
                if not cached.exists():
                    with Image.open(path) as im:
                        im.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
                        im = ImageOps.exif_transpose(im).convert("RGB")
                        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
                        tmp = cached.with_suffix(".tmp")
                        im.save(tmp, "JPEG", quality=80)
                        tmp.replace(cached)
        return cached.read_bytes()

    def screen_png():
        """The last frame as the panel shows it (dithered to its colours), as PNG."""
        with app.lock:
            img, version = app.last_image, app.screen_version
        if img is None:
            return None
        with screen_lock:
            if screen_cache["version"] != version:
                from .display import _flat, preview_rgb
                from .photo import dither
                if img.mode != "P":
                    img = dither(img, app.display.saturation)
                img = img.copy()
                img.putpalette(_flat(preview_rgb(app.display.panel)))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "PNG")
                screen_cache.update(version=version, png=buf.getvalue())
            return screen_cache["png"]

    def unique(path):
        if not path.exists():
            return path
        for n in range(1, 1000):
            candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise BadRequest("too many files with that name")

    class Handler(BaseHTTPRequestHandler):
        server_version = "InkFrame"

        def log_message(self, fmt, *args):
            log.debug("%s " + fmt, self.address_string(), *args)

        # ---------- plumbing ----------
        def _send(self, body, ctype="application/json", status=HTTPStatus.OK, cache=False):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                raise BadRequest("too large")
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise BadRequest("bad JSON")

        def _dispatch(self, routes):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            route = routes.get(url.path)
            try:
                if route is None:
                    raise BadRequest("not found", HTTPStatus.NOT_FOUND)
                route(query)
            except BadRequest as exc:
                self._send({"error": str(exc)}, status=exc.status)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                log.exception("web request %s failed", self.path)
                self._send({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_GET(self):
            self._dispatch({
                "/": lambda q: self._send((STATIC / "index.html").read_bytes(),
                                          "text/html; charset=utf-8"),
                "/api/status": lambda q: self._send(status()),
                "/api/folders": lambda q: self._send(folders()),
                "/api/list": lambda q: self._send(listing(q.get("folder", ""))),
                "/api/thumb": lambda q: self._send(thumbnail(q.get("path")), "image/jpeg",
                                                   cache=True),
                "/api/screen.png": self._screen,
            })

        def do_POST(self):
            self._dispatch({
                "/api/command": self._command,
                "/api/mkdir": self._mkdir,
                "/api/delete": self._delete,
            })

        def do_PUT(self):
            self._dispatch({"/api/upload": self._upload})

        # ---------- routes ----------
        def _screen(self, query):
            png = screen_png()
            if png is None:
                self._send(b"", "image/png", HTTPStatus.NO_CONTENT)
            else:
                self._send(png, "image/png")

        def _command(self, query):
            body = self._json_body()
            cmd = body.get("cmd")
            if cmd == "show":
                path = resolve(body.get("path"))
                if not path.is_file() or not is_photo(path):
                    raise BadRequest("not a photo")
                body = {"cmd": "show", "path": str(path)}
            elif cmd == "slideshow":
                settings = body.get("settings") or {}
                if "folder" in settings:
                    folder = resolve(settings["folder"])
                    if not folder.is_dir():
                        raise BadRequest("not a folder")
                    settings["folder"] = relative(folder) if folder != root() else ""
                body = {"cmd": "slideshow", "settings": settings}
            elif cmd not in ("mode", "next", "redraw", "clean"):
                raise BadRequest("unknown command")
            app.presses.put(body)
            self._send({"ok": True, "busy": app.busy})

        def _mkdir(self, query):
            body = self._json_body()
            parent = resolve(body.get("folder", ""))
            path = parent / clean_name(body.get("name"))
            if path.exists():
                raise BadRequest("already exists")
            path.mkdir()
            self._send({"ok": True, "path": relative(path)})

        def _delete(self, query):
            path = resolve(self._json_body().get("path"))
            if path == root():
                raise BadRequest("can't delete the photo folder")
            if path.is_dir():
                if any(path.iterdir()):
                    raise BadRequest("folder isn't empty")
                path.rmdir()
            elif is_photo(path):
                path.unlink()
            else:
                raise BadRequest("not a photo")
            self._send({"ok": True})

        def _upload(self, query):
            folder = resolve(query.get("folder", ""))
            if not folder.is_dir():
                raise BadRequest("not a folder")
            name = clean_name(Path(query.get("name", "")).name)
            if not is_photo(Path(name)):
                raise BadRequest("not a photo type InkFrame can show")
            length = int(self.headers.get("Content-Length") or -1)
            if length < 0:
                raise BadRequest("Content-Length required", HTTPStatus.LENGTH_REQUIRED)
            if length > MAX_UPLOAD:
                raise BadRequest("file too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            dest = unique(folder / name)
            tmp = folder / f".upload-{dest.name}"
            try:
                with open(tmp, "wb") as f:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(remaining, 1 << 16))
                        if not chunk:
                            raise BadRequest("upload cut short")
                        f.write(chunk)
                        remaining -= len(chunk)
                try:
                    with Image.open(tmp) as im:
                        im.verify()
                except Exception:
                    raise BadRequest(f"{name} isn't an image InkFrame can read")
                tmp.replace(dest)
            finally:
                tmp.unlink(missing_ok=True)
            log.info("uploaded %s", dest)
            self._send({"ok": True, "path": relative(dest)})

    return Handler

