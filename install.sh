#!/usr/bin/env bash
# Set up InkFrame on a Raspberry Pi and install it as a service that starts on boot.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${VENV:-$HOME/inky-env}"

echo "== System packages"
sudo apt-get update
sudo apt-get install -y git python3-venv python3-dev build-essential libopenblas0 fonts-dejavu-core

echo "== SPI and I2C for the Inky board"
REBOOT=0
if command -v raspi-config > /dev/null; then
  sudo raspi-config nonint do_spi 0
  sudo raspi-config nonint do_i2c 0
fi
BOOT_CONFIG=/boot/firmware/config.txt
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG=/boot/config.txt
if [ -f "$BOOT_CONFIG" ] && ! grep -q "^dtoverlay=spi0-0cs" "$BOOT_CONFIG"; then
  # The Inky driver drives chip select itself, so the SPI driver must not claim it.
  echo "dtoverlay=spi0-0cs" | sudo tee -a "$BOOT_CONFIG" > /dev/null
  REBOOT=1
fi
[ -e /dev/spidev0.0 ] && [ -e /dev/i2c-1 ] || REBOOT=1

echo "== Python environment in $VENV"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -r "$DIR/requirements.txt"

echo "== Config and photo folder"
[ -f "$DIR/config.toml" ] || cp "$DIR/config.example.toml" "$DIR/config.toml"
mkdir -p "$HOME/Pictures/inkframe"

echo "== Service"
sed -e "s|@USER@|$USER|" -e "s|@DIR@|$DIR|" -e "s|@VENV@|$VENV|" "$DIR/inkframe.service" \
  | sudo tee /etc/systemd/system/inkframe.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable inkframe

echo
echo "Done. Next:"
echo "  1. Set your latitude/longitude in $DIR/config.toml"
echo "  2. Copy photos into $HOME/Pictures/inkframe"
if [ "$REBOOT" = 1 ]; then
  echo "  3. sudo reboot   (needed once, for the SPI/I2C changes; InkFrame starts by itself afterwards)"
else
  echo "  3. sudo systemctl start inkframe"
fi
