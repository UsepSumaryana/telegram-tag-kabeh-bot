#!/usr/bin/env bash
# Install / update bot sebagai service systemd (Ubuntu/Debian).
# Jalankan dari folder proyek di VPS:  sudo bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/tagall-bot
APP_USER=tagallbot
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Jalankan dengan sudo." >&2
  exit 1
fi

apt-get update -qq
apt-get install -y -qq python3 python3-venv >/dev/null

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

mkdir -p "$APP_DIR/data"
install -m 644 "$SRC_DIR/bot.py" "$SRC_DIR/requirements.txt" "$APP_DIR/"

if [[ ! -f "$APP_DIR/.env" ]]; then
  if [[ -f "$SRC_DIR/.env" ]]; then
    install -m 600 "$SRC_DIR/.env" "$APP_DIR/.env"
  else
    install -m 600 "$SRC_DIR/.env.example" "$APP_DIR/.env"
    echo ">> Isi BOT_TOKEN di $APP_DIR/.env lalu jalankan: sudo systemctl restart tagall-bot"
  fi
fi

[[ -d "$APP_DIR/.venv" ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

install -m 644 "$SRC_DIR/deploy/tagall-bot.service" /etc/systemd/system/tagall-bot.service
systemctl daemon-reload
systemctl enable tagall-bot >/dev/null
systemctl restart tagall-bot

sleep 2
systemctl --no-pager status tagall-bot | head -n 10
echo ">> Log: sudo journalctl -u tagall-bot -f"
