"""Load config.toml and fill in defaults for anything left out."""
import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "app": {
        "start_mode": "weather",
        "state_dir": "~/.local/state/inkframe",
    },
    "display": {
        "saturation": 0.5,      # 0..1, how hard photos are pushed toward the panel's colours
        "rotate": 0,            # 0 or 180 if the frame is mounted upside down
        "clean_every": 48,      # run a clean cycle every N refreshes (0 = never)
        "quiet_hours": [],      # e.g. [23, 7] = no timed refreshes 23:00-07:00
    },
    "buttons": {
        "enabled": True,
    },
    "weather": {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "location_name": "",
        "units": "imperial",    # or "metric"
        "interval_minutes": 30,
    },
    "slideshow": {
        "folder": "~/Pictures/inkframe",
        "interval_minutes": 15,
        "fit": "crop",          # "crop" fills the screen, "fit" shows the whole photo
        "colour_boost": 1.3,    # extra saturation before dithering (1.0 = none)
        "contrast_boost": 1.1,
    },
    "potd": {
        "interval_minutes": 60,  # how often to check whether the day's picture has changed
        "caption": True,         # title and credit strip along the bottom
        "language": "en",        # caption language, if Commons has one
        "fit": "auto",           # "auto" crops unless that would cut off a lot
        "colour_boost": 1.2,
        "contrast_boost": 1.1,
    },
}


def _merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load(path):
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path).expanduser()
    if path.exists():
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    cfg["app"]["state_dir"] = Path(cfg["app"]["state_dir"]).expanduser()
    cfg["app"]["state_dir"].mkdir(parents=True, exist_ok=True)
    cfg["slideshow"]["folder"] = Path(cfg["slideshow"]["folder"]).expanduser()
    return cfg
