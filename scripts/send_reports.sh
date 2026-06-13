#!/bin/bash
# Telegram push — run on Hetzner at 08:00 PM Peru time (01:00 UTC next day)
# Crontab entry: 0 1 * * * /path/to/sports-agent/scripts/send_reports.sh

set -e
cd "$(dirname "$0")/.."

echo "[$(date)] Sending Telegram reports..."
python tools/telegram_send.py
echo "[$(date)] Done"
