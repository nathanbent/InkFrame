from .slideshow import SlideshowMode
from .weather import WeatherMode

MODES = {m.name: m for m in (WeatherMode, SlideshowMode)}
