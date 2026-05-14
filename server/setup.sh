#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="netatmo-proxy"
SERVICE_FILE="$SCRIPT_DIR/$SERVICE_NAME.service"
INSTALL_PATH="/etc/systemd/system/$SERVICE_NAME.service"
ACTUAL_HOME="$(eval echo ~"${SUDO_USER:-$USER}")"

echo "=== Netatmo Home Hub — server setup ==="

# Dependencies
sudo apt-get update -q
sudo apt-get install -y python3-pip python3-venv

# Virtual env
cd "$SCRIPT_DIR"
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt
echo "Python dependencies installed."

# Patch home dir in service file if needed, then install
sudo sed "s|/home/pi|$ACTUAL_HOME|g" "$SERVICE_FILE" | sudo tee "$INSTALL_PATH" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo "Systemd service installed and enabled."

echo ""
echo "Next steps:"
echo "  1. cp $SCRIPT_DIR/config.example.env $SCRIPT_DIR/.env"
echo "  2. Edit .env with your Netatmo credentials"
echo "  3. sudo systemctl start $SERVICE_NAME"
echo "  4. sudo journalctl -u $SERVICE_NAME -f    # watch logs"
