# InkFrame

A weather dashboard and photo slideshow for the Pimoroni Inky Impression 5.7" (7-colour, 600×448), running on a Raspberry Pi Zero W.

![Weather dashboard preview](docs/weather.png)

## Buttons

| Button | Action |
| --- | --- |
| A | Weather dashboard |
| B | Photo slideshow (press again for the next photo) |
| C | Refresh now (next photo in slideshow mode) |
| D | Clean the screen to reduce ghosting, then redraw |

A refresh takes about 30 seconds. Presses made during a refresh are not lost: the most recent one runs when the refresh finishes.

## Install

Enable SPI and I2C, and on current Raspberry Pi OS add `dtoverlay=spi0-0cs` to `/boot/firmware/config.txt`, then reboot. Then:

```bash
git clone <this repo> ~/inkframe
cd ~/inkframe
./install.sh
nano config.toml          # set latitude / longitude
sudo systemctl start inkframe
```

`install.sh` reuses the virtual environment at `~/inky-env` (override with `VENV=...`), copies `config.example.toml` to `config.toml`, creates `~/Pictures/inkframe`, and installs a systemd service that starts on boot.

## Photos

Put photos in `~/Pictures/inkframe` (subfolders work). From another computer:

```bash
scp *.jpg nathan@<pi-address>:~/Pictures/inkframe/
```

Photos are shown in a random order with no repeats until every photo has been shown. New photos are picked up automatically. Bright, high-contrast images with large areas of colour look best on this panel; fine detail and skin tones dither heavily.

## Running by hand

Stop the service first so two programs don't fight over the display:

```bash
sudo systemctl stop inkframe
~/inky-env/bin/python -m inkframe --once --mode weather     # draw one frame and exit
~/inky-env/bin/python -m inkframe --once --clean            # clean, then draw
~/inky-env/bin/python -m inkframe --dry-run --once          # no display: writes a PNG to ./preview
```

`--dry-run` also works on a normal computer with Pillow installed, for working on layouts.

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
```

A new mode is a class with `name`, `interval` (seconds), `next()` and `render(size)` returning a Pillow image, registered in `modes/__init__.py`. Draw dashboards on `display.new_canvas()` using the palette constants (`BLACK`, `RED`, ...) for crisp, undithered colours; return an RGB image for photos.
