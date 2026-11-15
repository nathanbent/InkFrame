"""Weather dashboard using Open-Meteo (free, no API key)."""
import json
import logging
import math
import urllib.parse
import urllib.request
from datetime import datetime

from PIL import ImageDraw

from ..display import BLACK, BLUE, GREEN, ORANGE, RED, WHITE, YELLOW, new_canvas
from ..fonts import font

log = logging.getLogger(__name__)

API = "https://api.open-meteo.com/v1/forecast"

# WMO weather code -> (label, icon kind)
CODES = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "partly"), 2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloudy"), 45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"), 80: ("Showers", "rain"), 81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"), 85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm", "storm"), 99: ("Thunderstorm", "storm"),
}
KIND_COLOUR = {"clear": ORANGE, "partly": ORANGE, "cloudy": BLACK, "fog": BLACK,
               "rain": BLUE, "snow": BLUE, "storm": RED}


class WeatherMode:
    name = "weather"

    def __init__(self, cfg, state_dir):
        self.cfg = cfg["weather"]
        self.cache = state_dir / "weather.json"
        self.interval = self.cfg["interval_minutes"] * 60
        metric = self.cfg["units"] == "metric"
        self.temp_unit = "celsius" if metric else "fahrenheit"
        self.wind_unit = "kmh" if metric else "mph"
        self.wind_label = "km/h" if metric else "mph"

    def next(self):
        pass

    # ---------- data ----------
    def fetch(self):
        params = {
            "latitude": self.cfg["latitude"],
            "longitude": self.cfg["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                       "weather_code,wind_speed_10m,is_day",
            "hourly": "temperature_2m,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,sunrise,sunset",
            "temperature_unit": self.temp_unit,
            "wind_speed_unit": self.wind_unit,
            "timezone": "auto",
            "forecast_days": 4,
        }
        url = API + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.load(resp)
            self.cache.write_text(json.dumps(data))
            return data, False
        except Exception as exc:
            log.warning("weather fetch failed (%s); using cached data", exc)
            if self.cache.exists():
                return json.loads(self.cache.read_text()), True
            raise

    # ---------- drawing ----------
    def render(self, size):
        data, stale = self.fetch()
        img = new_canvas(size)
        d = ImageDraw.Draw(img)
        w, h = size
        cur, daily, hourly = data["current"], data["daily"], data["hourly"]
        now = datetime.fromisoformat(cur["time"])

        # Header bar
        d.rectangle((0, 0, w, 36), fill=BLACK)
        d.text((12, 18), now.strftime("%A, %B ") + str(now.day), font=font(20, True),
               fill=WHITE, anchor="lm")
        right = self.cfg["location_name"] or ""
        right = (right + "  " if right else "") + "Updated " + _clock(now)
        if stale:
            right = "OFFLINE - " + right
        d.text((w - 12, 18), right, font=font(15), fill=WHITE, anchor="rm")

        # Current conditions
        label, kind = CODES.get(cur["weather_code"], ("Unknown", "cloudy"))
        draw_icon(d, kind, 16, 50, 130, night=not cur.get("is_day", 1))
        d.text((160, 44), f"{round(cur['temperature_2m'])}°", font=font(100, True), fill=BLACK)
        d.text((164, 160), label, font=font(26, True), fill=KIND_COLOUR[kind])

        # Detail column
        x, y = 420, 46
        rows = [
            ("High", f"{round(daily['temperature_2m_max'][0])}°", RED),
            ("Low", f"{round(daily['temperature_2m_min'][0])}°", BLUE),
            ("Rain", f"{daily['precipitation_probability_max'][0] or 0}%", BLUE),
            ("Feels", f"{round(cur['apparent_temperature'])}°", BLACK),
            ("Wind", f"{round(cur['wind_speed_10m'])} {self.wind_label}", BLACK),
            ("Humid", f"{cur['relative_humidity_2m']}%", BLACK),
        ]
        for name, value, colour in rows:
            d.text((x, y), name, font=font(18), fill=BLACK)
            d.text((w - 14, y), value, font=font(18, True), fill=colour, anchor="ra")
            y += 24
        sunrise = datetime.fromisoformat(daily["sunrise"][0])
        sunset = datetime.fromisoformat(daily["sunset"][0])
        d.text((w - 14, y + 2), f"Sun {_clock(sunrise)} - {_clock(sunset)}", font=font(14),
               fill=ORANGE, anchor="ra")

        # 24-hour chart
        self._chart(d, hourly, now, (44, 226, w - 16, 320))
        d.line((0, 340, w, 340), fill=BLACK, width=2)

        # Next three days
        col = w // 3
        for i in range(1, 4):
            cx = col * (i - 1) + col // 2
            day = datetime.fromisoformat(daily["time"][i])
            _, k = CODES.get(daily["weather_code"][i], ("", "cloudy"))
            d.text((cx, 350), day.strftime("%A"), font=font(18, True), fill=BLACK, anchor="ma")
            draw_icon(d, k, cx - 75, 376, 60)
            d.text((cx + 2, 386), f"{round(daily['temperature_2m_max'][i])}°",
                   font=font(22, True), fill=RED)
            d.text((cx + 2, 412), f"{round(daily['temperature_2m_min'][i])}°",
                   font=font(18), fill=BLUE)
            pop = daily["precipitation_probability_max"][i]
            if pop:
                d.text((cx + 70, 416), f"{pop}%", font=font(14), fill=BLUE, anchor="ra")
            if i < 3:
                d.line((col * i, 348, col * i, h - 8), fill=BLACK)
        return img

    def _chart(self, d, hourly, now, box):
        x0, y0, x1, y1 = box
        hour = now.replace(minute=0, second=0, microsecond=0)
        times = [datetime.fromisoformat(t) for t in hourly["time"]]
        start = next((i for i, t in enumerate(times) if t >= hour), 0)
        temps = hourly["temperature_2m"][start:start + 25]
        pops = hourly["precipitation_probability"][start:start + 25]
        hours = times[start:start + 25]
        if len(temps) < 2:
            return
        lo, hi = math.floor(min(temps)), math.ceil(max(temps))
        if hi - lo < 4:
            hi, lo = hi + 2, lo - 2
        step = (x1 - x0) / (len(temps) - 1)

        d.text((x0 - 32, y0 - 20), "Next 24 hours", font=font(14, True), fill=BLACK)
        d.rectangle((x0 + 92, y0 - 17, x0 + 102, y0 - 7), fill=BLUE)
        d.text((x0 + 107, y0 - 20), "chance of rain", font=font(13), fill=BLUE)
        # Rain-chance bars behind the line
        for i, p in enumerate(pops):
            if p:
                bx = x0 + i * step
                d.rectangle((bx - step * 0.35, y1 - (y1 - y0) * p / 100, bx + step * 0.35, y1),
                            fill=BLUE)
        d.line((x0, y1, x1, y1), fill=BLACK)
        # Temperature line
        pts = [(x0 + i * step, y1 - (t - lo) / (hi - lo) * (y1 - y0)) for i, t in enumerate(temps)]
        d.line(pts, fill=RED, width=3, joint="curve")
        d.text((x0 - 6, y0), f"{hi}°", font=font(13), fill=BLACK, anchor="ra")
        d.text((x0 - 6, y1), f"{lo}°", font=font(13), fill=BLACK, anchor="rd")
        for i, t in enumerate(hours):
            if i % 6 == 0:
                bx = x0 + i * step
                d.line((bx, y1, bx, y1 + 4), fill=BLACK)
                d.text((bx, y1 + 6), "Now" if i == 0 else _clock(t, short=True),
                       font=font(13), fill=BLACK, anchor="ma")


def _clock(t, short=False):
    if short:
        return t.strftime("%I%p").lstrip("0").lower()
    return t.strftime("%I:%M %p").lstrip("0")


# ---------- icons, drawn with shapes so no image files are needed ----------
def draw_icon(d, kind, x, y, s, night=False):
    if kind == "clear":
        (_moon if night else _sun)(d, x + s / 2, y + s / 2, s * 0.28)
    elif kind == "partly":
        if night:
            _moon(d, x + s * 0.38, y + s * 0.36, s * 0.2)
        else:
            _sun(d, x + s * 0.38, y + s * 0.36, s * 0.2)
        _cloud(d, x + s * 0.2, y + s * 0.38, s * 0.75)
    elif kind == "cloudy":
        _cloud(d, x + s * 0.08, y + s * 0.2, s * 0.85)
    elif kind == "fog":
        for i in range(4):
            yy = y + s * (0.3 + i * 0.13)
            d.line((x + s * (0.15 + 0.05 * (i % 2)), yy, x + s * 0.85, yy), fill=BLACK,
                   width=max(2, int(s / 22)))
    else:
        _cloud(d, x + s * 0.08, y + s * 0.08, s * 0.85)
        top = y + s * 0.68
        if kind == "rain":
            for i in range(4):
                bx = x + s * (0.25 + i * 0.16)
                d.line((bx, top, bx - s * 0.06, top + s * 0.2), fill=BLUE,
                       width=max(2, int(s / 20)))
        elif kind == "snow":
            r = max(2, s / 28)
            for i in range(4):
                bx, by = x + s * (0.25 + i * 0.16), top + s * (0.06 if i % 2 else 0.16)
                d.ellipse((bx - r, by - r, bx + r, by + r), fill=BLUE)
        elif kind == "storm":
            cx = x + s * 0.5
            bolt = [(cx + s * 0.02, top - s * 0.06), (cx - s * 0.12, top + s * 0.14),
                    (cx, top + s * 0.14), (cx - s * 0.08, top + s * 0.3),
                    (cx + s * 0.14, top + s * 0.06), (cx + s * 0.02, top + s * 0.06)]
            d.polygon(bolt, fill=YELLOW, outline=BLACK)


def _sun(d, cx, cy, r):
    for i in range(8):
        a = i * math.pi / 4
        d.line((cx + math.cos(a) * r * 1.3, cy + math.sin(a) * r * 1.3,
                cx + math.cos(a) * r * 1.65, cy + math.sin(a) * r * 1.65),
               fill=ORANGE, width=max(2, int(r / 6)))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=YELLOW, outline=ORANGE, width=max(2, int(r / 8)))


def _moon(d, cx, cy, r):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=YELLOW, outline=ORANGE, width=max(2, int(r / 8)))
    o = r * 0.55
    d.ellipse((cx - r + o, cy - r - o * 0.4, cx + r + o, cy + r - o * 0.4), fill=WHITE)


def _cloud(d, x, y, s):
    lw = max(2, int(s / 30))
    parts = [
        ("e", (x + s * 0.05, y + s * 0.30, x + s * 0.45, y + s * 0.65)),
        ("e", (x + s * 0.25, y + s * 0.10, x + s * 0.70, y + s * 0.55)),
        ("e", (x + s * 0.55, y + s * 0.28, x + s * 0.95, y + s * 0.65)),
        ("r", (x + s * 0.25, y + s * 0.40, x + s * 0.75, y + s * 0.65)),
    ]
    # outline pass, then fill pass, so overlapping shapes merge into one outline
    for grow, colour in ((lw, BLACK), (0, WHITE)):
        for shape, (a, b, c, e) in parts:
            box = (a - grow, b - grow, c + grow, e + grow)
            (d.ellipse if shape == "e" else d.rectangle)(box, fill=colour)
