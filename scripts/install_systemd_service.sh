#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/opt/telegram_reminder_mvp}"
SERVICE_NAME="${2:-reminder-bot}"
CURRENT_USER="$(whoami)"

sudo mkdir -p "$APP_DIR"
sudo cp -R . "$APP_DIR"
sudo chown -R "$CURRENT_USER":"$CURRENT_USER" "$APP_DIR"

sudo cp deployment/systemd/reminder-bot.service "/etc/systemd/system/${SERVICE_NAME}@.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}@${CURRENT_USER}"
sudo systemctl restart "${SERVICE_NAME}@${CURRENT_USER}"
sudo systemctl status "${SERVICE_NAME}@${CURRENT_USER}" --no-pager
