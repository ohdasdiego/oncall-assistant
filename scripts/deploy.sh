#!/bin/bash
# deploy.sh — On-Call Assistant VPS deployment
set -e

APP_DIR="/opt/oncall-assistant"
SERVICE="oncall-assistant"
LOG_DIR="/var/log/oncall-assistant"
DATA_DIR="/var/lib/oncall-assistant"

echo "── On-Call Assistant Deployment ──"

# Directories
sudo mkdir -p "$APP_DIR" "$LOG_DIR" "$DATA_DIR"
sudo chown -R www-data:www-data "$LOG_DIR" "$DATA_DIR"

# Copy files
sudo rsync -av --exclude='.git' --exclude='venv' --exclude='__pycache__' \
    ./ "$APP_DIR/"

# Virtualenv
if [ ! -d "$APP_DIR/venv" ]; then
    sudo python3 -m venv "$APP_DIR/venv"
fi
sudo "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# .env
if [ ! -f "$APP_DIR/.env" ]; then
    sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "WARNING: Created .env from template -- edit $APP_DIR/.env before starting"
fi

# Systemd
sudo cp systemd/$SERVICE.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"

# Nginx
sudo cp scripts/$SERVICE.nginx.conf /etc/nginx/sites-available/$SERVICE
sudo ln -sf /etc/nginx/sites-available/$SERVICE /etc/nginx/sites-enabled/$SERVICE
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "Deployed. Check status:"
echo "  sudo systemctl status $SERVICE"
echo "  sudo journalctl -u $SERVICE -f"
