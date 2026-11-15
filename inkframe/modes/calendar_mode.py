"""Calendar: month grid plus the next few days' events from one or more iCal (ICS) links.

Works with Google Calendar's "Secret address in iCal format", and any other
calendar that publishes an .ics link. Recurring events are expanded properly.
"""
import calendar
import hashlib
import json
import logging
import time as _time
import urllib.request
from datetime import date, datetime, time, timedelta

from PIL import ImageDraw

from ..display import BLACK, BLUE, GREEN, ORANGE, RED, WHITE, new_canvas
from ..fonts import font
from .weather import CODES, _clock, draw_icon

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "InkFrame/1.0 (Raspberry Pi e-ink calendar)"}
SOURCE_COLOURS = [BLUE, RED, GREEN, ORANGE, BLACK]
COLOUR_NAMES = {"black": BLACK, "blue": BLUE, "red": RED, "green": GREEN, "orange": ORANGE}


class CalendarMode:
    name = "calendar"

    def __init__(self, cfg, state_dir):
        self.cfg = cfg["calendar"]
        self.interval = self.cfg["interval_minutes"] * 60
        self.state_dir = state_dir
        self.dir = state_dir / "calendar"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache = None
        self.shown = None

    def next(self):
        self._cache = None          # button pressed again: fetch fresh

    def changed(self):
        """Timed refreshes only redraw when the events (or the day) have changed."""
        try:
            return self._signature(*self._load()) != self.shown
        except Exception:
            return True

    # ---------- data ----------
    def _sources(self):
        out = []
        for i, src in enumerate(self.cfg["sources"]):
            if isinstance(src, str):
                src = {"url": src}
            colour = COLOUR_NAMES.get(str(src.get("colour", "")).lower(),
                                      SOURCE_COLOURS[i % len(SOURCE_COLOURS)])
            out.append((i, src["url"], colour))
        return out

    def _download(self):
        texts, stale = [], False
        for i, url, colour in self._sources():
            cached = self.dir / f"{i}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.ics"
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                _check_ics(data, i)
                cached.write_bytes(data)
            except Exception as exc:
                if not cached.exists() or not _is_ics(cached.read_bytes()):
                    raise RuntimeError(f"Calendar {i + 1}: {_short(exc)}") from None
                log.warning("calendar %d fetch failed (%s); using cached copy", i + 1, _short(exc))
                data, stale = cached.read_bytes(), True
            texts.append((data, colour))
        return texts, stale

    def _load(self):
        """Return (events, stale). Cached for two minutes so changed() + render() fetch once."""
        if self._cache and _time.monotonic() - self._cache[0] < 120:
            return self._cache[1], self._cache[2]
        import icalendar
        import recurring_ical_events

        texts, stale = self._download()
        now = datetime.now()
        today = now.date()
        first = today.replace(day=1)
        last = max(today + timedelta(days=self.cfg["days"]),
                   first.replace(day=calendar.monthrange(first.year, first.month)[1]) + timedelta(days=1))
        events = []
        for data, colour in texts:
            cal = icalendar.Calendar.from_ical(data)
            for ev in recurring_ical_events.of(cal).between(first, last):
                if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                start, stop, all_day = _span(ev)
                events.append({"start": start, "stop": stop, "all_day": all_day, "colour": colour,
                               "title": str(ev.get("SUMMARY", "")).strip() or "(no title)"})
        events.sort(key=lambda e: (e["start"].date(), not e["all_day"], e["start"], e["title"]))
        self._cache = (_time.monotonic(), events, stale)
        return events, stale

    def _agenda(self, events):
        """Events from now through the configured number of days, grouped by day."""
        now = datetime.now()
        today = now.date()
        end = today + timedelta(days=self.cfg["days"])
        days = {}
        for e in events:
            if e["stop"] <= now or e["start"].date() >= end:
                continue
            day = max(e["start"].date(), today)
            days.setdefault(day, []).append(e)
        for day in days:
            days[day].sort(key=lambda e: (not e["all_day"], e["start"], e["title"]))
        return dict(sorted(days.items()))

    def _signature(self, events, stale):
        agenda = self._agenda(events)
        flat = [(d.isoformat(), e["title"], e["start"].isoformat(), e["all_day"])
                for d, evs in agenda.items() for e in evs]
        return json.dumps([date.today().isoformat(), stale, flat])

    # ---------- drawing ----------
    def render(self, size):
        events, stale = self._load()
        self.shown = self._signature(events, stale)
        agenda = self._agenda(events)
        img = new_canvas(size)
        d = ImageDraw.Draw(img)
        w, h = size
        today = date.today()

        d.rectangle((0, 0, w, 36), fill=BLACK)
        d.text((12, 18), today.strftime("%A, %B ") + str(today.day), font=font(20, True),
               fill=WHITE, anchor="lm")
        if stale:
            d.text((w - 12, 18), "OFFLINE", font=font(15, True), fill=WHITE, anchor="rm")

        busy = {e["start"].date() for e in events}
        busy |= {day for day in agenda}
        self._month(d, today, busy, 10, 46)
        self._weather(d, 14, 300)
        d.line((240, 46, 240, h - 10), fill=BLACK)
        if not self.cfg["sources"]:
            d.multiline_text((254, 60), "No calendar linked yet.\n\nAdd your Google Calendar's\n"
                             "secret iCal address under\n[calendar] in config.toml.",
                             font=font(16), fill=BLACK, spacing=6)
        else:
            self._list(d, agenda, today, 254, 46, w - 12, h - 8)
        return img

    def _month(self, d, today, busy, x0, y0):
        first_weekday = 0 if self.cfg["week_start"] == "monday" else 6
        cal = calendar.Calendar(first_weekday)
        cw, ch = 31, 30
        d.text((x0 + cw * 3.5, y0), today.strftime("%B %Y"), font=font(18, True), fill=BLACK,
               anchor="ma")
        names = [calendar.day_abbr[(first_weekday + i) % 7][0] for i in range(7)]
        for i, n in enumerate(names):
            d.text((x0 + cw * i + cw / 2, y0 + 30), n, font=font(13, True),
                   fill=RED if n == "S" else BLACK, anchor="ma")
        y = y0 + 52
        for week in cal.monthdatescalendar(today.year, today.month):
            for i, day in enumerate(week):
                if day.month != today.month:
                    continue
                cx, cy = x0 + cw * i + cw / 2, y + ch / 2 - 3
                if day == today:
                    d.ellipse((cx - 13, cy - 13, cx + 13, cy + 13), fill=RED)
                d.text((cx, cy), str(day.day), font=font(15, day == today),
                       fill=WHITE if day == today else BLACK, anchor="mm")
                if day in busy and day != today:
                    d.ellipse((cx - 2, cy + 10, cx + 2, cy + 14), fill=BLUE)
            y += ch

    def _weather(self, d, x, y):
        """Today's forecast from the weather mode's cache, if it has run today."""
        try:
            data = json.loads((self.state_dir / "weather.json").read_text())
            daily = data["daily"]
            if daily["time"][0] != date.today().isoformat():
                return
        except Exception:
            return
        label, kind = CODES.get(daily["weather_code"][0], ("", "cloudy"))
        draw_icon(d, kind, x - 4, y, 70)
        d.text((x + 74, y + 8), f"{round(daily['temperature_2m_max'][0])}°", font=font(26, True),
               fill=RED)
        d.text((x + 140, y + 14), f"{round(daily['temperature_2m_min'][0])}°", font=font(20),
               fill=BLUE)
        d.text((x + 74, y + 44), label, font=font(15), fill=BLACK)
        pop = daily["precipitation_probability_max"][0]
        if pop:
            d.text((x + 74, y + 64), f"{pop}% chance of rain", font=font(13), fill=BLUE)

    def _list(self, d, agenda, today, x0, y0, x1, y1):
        if not agenda:
            d.text(((x0 + x1) / 2, (y0 + y1) / 2), "Nothing scheduled", font=font(22, True),
                   fill=BLACK, anchor="mm")
            return
        y = y0
        lines = [(day, e) for day, evs in agenda.items() for e in [None] + evs]
        for n, (day, e) in enumerate(lines):
            need = 28 + 23 if e is None else 23   # a day heading needs room for one event
            if y + need > y1 - (20 if n < len(lines) - 1 else 0):
                remaining = sum(1 for _, ev in lines[n:] if ev is not None)
                if remaining:
                    d.text((x1, y1), f"+{remaining} more", font=font(14, True), fill=BLACK,
                           anchor="rd")
                return
            if e is None:
                if y > y0:
                    y += 4
                if day == today:
                    title, colour = "Today", RED
                elif day == today + timedelta(days=1):
                    title, colour = "Tomorrow", BLACK
                else:
                    title, colour = day.strftime("%A, %b ") + str(day.day), BLACK
                d.text((x0, y), title, font=font(17, True), fill=colour)
                d.line((x0, y + 22, x1, y + 22), fill=colour)
                y += 28
            else:
                d.rectangle((x0, y + 2, x0 + 4, y + 18), fill=e["colour"])
                when = "All day" if e["all_day"] else _clock(e["start"])
                d.text((x0 + 10, y), when, font=font(15), fill=BLACK)
                title = e["title"]
                last_day = (e["stop"] - timedelta(seconds=1)).date()
                if last_day > day:
                    title += f" (until {last_day.strftime('%a')})"
                d.text((x0 + 88, y), _fit(d, title, font(16, True), x1 - x0 - 88),
                       font=font(16, True), fill=BLACK)
                y += 23


def _is_ics(data):
    return data.lstrip()[:15].upper().startswith(b"BEGIN:VCALENDAR")


def _check_ics(data, i):
    if _is_ics(data):
        return
    if b"<html" in data[:2000].lower() or b"<!doctype" in data[:2000].lower():
        raise ValueError("that link returned a web page (probably a sign-in page), not calendar "
                         "data. Use the calendar's 'Secret address in iCal format', which ends "
                         "in /basic.ics")
    raise ValueError("that link did not return iCal (.ics) data")


def _short(exc, limit=200):
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[:limit] + "..."


def _local(dt):
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def _span(ev):
    start = ev["DTSTART"].dt
    if ev.get("DTEND") is not None:
        stop = ev["DTEND"].dt
    elif ev.get("DURATION") is not None:
        stop = start + ev["DURATION"].dt
    else:
        stop = start + (timedelta(days=1) if not isinstance(start, datetime) else timedelta(0))
    if not isinstance(start, datetime):
        stop = stop if not isinstance(stop, datetime) else stop.date()
        return datetime.combine(start, time.min), datetime.combine(stop, time.min), True
    start, stop = _local(start), _local(stop if isinstance(stop, datetime) else
                                       datetime.combine(stop, time.min))
    return start, max(stop, start + timedelta(minutes=1)), False


def _fit(d, text, fnt, max_width):
    if d.textlength(text, font=fnt) <= max_width:
        return text
    while text and d.textlength(text + "…", font=fnt) > max_width:
        text = text[:-1]
    return text.rstrip(" ,.;:") + "…"
