# InkFrame

A weather dashboard, photo slideshow, Wikimedia picture of the day and calendar for the Pimoroni Inky Impression, running on a Raspberry Pi.

Supported panels (detected automatically):

| Panel | Resolution | Colours |
| --- | --- | --- |
| Inky Impression 7.3" (Spectra 6, 2025) | 800×480 | black, white, red, yellow, green, blue |
| Inky Impression 7.3" (2023) | 800×480 | the above plus orange |
| Inky Impression 5.7" | 600×448 | the above plus orange |

On the 6-colour Spectra panel, anything drawn in orange (the sun's rays, the sunrise/sunset line) is drawn in red instead.

![Weather dashboard preview](docs/weather.png)

## Buttons

| Button | Action |
| --- | --- |
| A | Weather dashboard |
| B | Photo slideshow |
| C | Wikimedia Commons Picture of the Day |
| D | Calendar |
| Hold D (2 s) | Clean the screen to reduce ghosting, then redraw (on the Spectra panel: black, then white, then redraw) |

Pressing the button for the mode that's already showing refreshes it: the next photo in the slideshow, or fresh weather or calendar data. Buttons act when released, so a hold can be told apart from a press.

A refresh takes about 30 seconds. Presses made during a refresh are not lost: the most recent one runs when the refresh finishes.

## Install

On a fresh Raspberry Pi OS (Bookworm or later), with the Inky board attached and the Pi on the network:

```bash
sudo apt-get install -y git
git clone https://github.com/nathanbent/InkFrame.git ~/inkframe
cd ~/inkframe
./install.sh
nano config.toml          # set latitude / longitude
sudo reboot
```

`install.sh` installs the system packages, enables SPI and I2C, adds `dtoverlay=spi0-0cs` to `/boot/firmware/config.txt` (the Inky driver needs the chip-select pin for itself), creates a Python virtual environment at `~/inky-env` (override with `VENV=...`), copies `config.example.toml` to `config.toml`, creates `~/Pictures/inkframe`, and installs a systemd service that starts on boot. The reboot is only needed the first time, for the SPI/I2C changes; after that, `sudo systemctl restart inkframe` picks up config changes.

If the board isn't detected (`couldn't identify the Inky board` in the log), check that the ribbon/header is seated, run `ls /dev/spidev0.0 /dev/i2c-1` to confirm SPI and I2C are on, or set `panel` under `[display]` in `config.toml` to skip detection.

## Portrait or upside down

Set `rotate` under `[display]` in `config.toml`, then `sudo systemctl restart inkframe`:

| `rotate` | Frame |
| --- | --- |
| `0` | landscape (default) |
| `180` | landscape, upside down |
| `90` or `270` | portrait: one is each way up, so if `90` comes out upside down, use `270` |

In portrait the weather lists the next 6 days with one day per row, and the calendar puts the month and today's weather above the events. Photos are cropped to the tall shape; set `fit = "fit"` under `[slideshow]` to show landscape photos whole.

## Photos

Put photos in `~/Pictures/inkframe` (subfolders work). From another computer:

```bash
scp *.jpg nathan@<pi-address>:~/Pictures/inkframe/
```

Photos are shown in a random order with no repeats until every photo has been shown. New photos are picked up automatically. Bright, high-contrast images with large areas of colour look best on these panels; fine detail and skin tones dither heavily. Photos are cropped to fill the screen (5:3 on the 7.3", 4:3 on the 5.7"); set `fit = "fit"` under `[slideshow]` to show them whole.

## Picture of the day

Button C shows [Wikimedia Commons' Picture of the Day](https://commons.wikimedia.org/wiki/Commons:Picture_of_the_day) with its caption and credit along the bottom. It's downloaded once a day and cached in `~/.local/state/inkframe/potd/`. The mode checks hourly but only redraws the screen when a new day's picture arrives, so it isn't flashing for nothing. Panoramas and very tall pictures are shown whole with white bars rather than cropped; set `fit = "crop"` under `[potd]` to always fill the screen, or `caption = false` to hide the caption.

## Calendar

Button D shows this month with today circled, today's forecast (from the weather mode's last update), and the next 7 days of events. Recurring events, time zones, all-day and multi-day events are all handled; cancelled events and ones that have already ended are hidden.

To connect Google Calendar, open [calendar.google.com](https://calendar.google.com) on a computer, go to **Settings**, pick the calendar under **Settings for my calendars**, then **Integrate calendar**, and copy **Secret address in iCal format**. Add it to `config.toml`:

```toml
[calendar]
sources = [
  "https://calendar.google.com/calendar/ical/.../basic.ics",
  { url = "https://calendar.google.com/calendar/ical/.../basic.ics", colour = "green" },
]
```

Each calendar gets its own colour marker (blue, red, green, orange, black, or set `colour`). The secret address works like a password, which is why it lives in `config.toml` (not committed to git). Event times use the Pi's time zone; check it with `timedatectl` and set it with `sudo timedatectl set-timezone America/New_York` (or yours). Google can take several hours to publish changes to this link.

The calendar checks every 5 minutes but only redraws when something changed, such as a new event or one finishing.

## Running by hand

Stop the service first so two programs don't fight over the display:

```bash
sudo systemctl stop inkframe
~/inky-env/bin/python -m inkframe --once --mode weather     # draw one frame and exit
~/inky-env/bin/python -m inkframe --once --clean            # clean, then draw
~/inky-env/bin/python -m inkframe --dry-run --once          # no display: writes a PNG to ./preview
```

`--dry-run` also works on a normal computer with Pillow installed, for working on layouts. It previews the 7.3" Spectra panel unless `panel` is set under `[display]`.

## Refresh cycling

`refresh_cycle.py` (7-colour panels only) is a standalone script that runs the panel through a long series of colour and pattern refreshes, to exercise a panel whose colours have faded or ghosted. By default it refreshes every 2 minutes for 24 hours. Stop the service first:

```bash
sudo systemctl stop inkframe
nohup ~/inky-env/bin/python refresh_cycle.py > refresh.log 2>&1 &
```

Frames come in blocks of 8: one layout shown 7 times with the colours rotated each time, so every pixel is driven to each of the 7 colours exactly once, followed by a clean pass. Every 4th block is the plain solid-colour sweep (the whole panel black, white, green, blue, red, yellow, orange); the others cycle through about 20 patterns (stripes, checkerboards, rings, noise, voronoi cells, random rectangles and so on) in shuffled order with random sizes. The colour order changes each block so that every colour-to-colour transition is used equally often. Frames are derived from the clock, so several frames started with the same `--interval` and `--seed` show the same thing at the same moment without talking to each other, as long as their clocks are synced. Stopping it (`kill`, or Ctrl-C) finishes the current refresh, cleans the panel and leaves it white; afterwards run `sudo systemctl start inkframe`. `--hours`, `--interval` and `--preview DIR` (PNGs, no panel needed) are the main options.

## Logs

```bash
journalctl -u inkframe -f
```

A copy is kept in `~/.local/state/inkframe/inkframe.log`, along with the last weather data (used when the network is down) and slideshow position.

## Layout

```
inkframe/
  app.py            main loop, buttons -> actions, timers
  display.py        Inky wrapper, palette, dry-run previews
  buttons.py        side buttons via libgpiod
  modes/weather.py  Open-Meteo dashboard (no API key needed)
  modes/slideshow.py
  modes/potd.py     Wikimedia Commons picture of the day
  modes/calendar_mode.py  month grid + agenda from iCal links
  photo.py          shared crop / colour-boost / dither helpers
```

A new mode is a class with `name`, `interval` (seconds), `next()` and `render(size)` returning a Pillow image, registered in `modes/__init__.py` and given a button in `BUTTON_MODES` in `app.py`. An optional `changed()` returning `False` lets timed refreshes skip redrawing. Draw dashboards on `display.new_canvas()` using the palette constants (`BLACK`, `RED`, ...) for crisp, undithered colours; return an RGB image for photos.
