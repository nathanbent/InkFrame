#!/usr/bin/env bash
# Set up InkFrame on a Raspberry Pi and install it as a service that starts on boot.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${VENV:-$HOME/inky-env}"

echo "== System packages"
sudo apt-get install -y python3-venv python3-dev build-essential libopenblas0 fonts-dejavu-core

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
echo "  3. sudo systemctl start inkframe"
