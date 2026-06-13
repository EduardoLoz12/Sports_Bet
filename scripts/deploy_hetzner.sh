#!/bin/bash
# Run on Hetzner VPS as root or deploy user.
# Usage: bash deploy_hetzner.sh

set -e

PROJECT_DIR="/opt/sports-agent"
REPO_URL=""   # fill if using git, else rsync from local
PYTHON="python3"

echo "=== 1. Dependencies ==="
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv sqlite3 curl

echo "=== 2. Project directory ==="
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

echo "=== 3. Python venv ==="
python3 -m venv venv
source venv/bin/activate
pip install --quiet -r requirements.txt

echo "=== 4. Crontab ==="
# Two jobs:
#   11:00 UTC (06:00 PE) — daily data pipeline
#   01:00 UTC (20:00 PE prev day) — telegram send
CRON_DATA="0 11 * * * cd $PROJECT_DIR && bash scripts/run_daily.sh >> logs/cron.log 2>&1"
CRON_SEND="0 1 * * * cd $PROJECT_DIR && venv/bin/python -u tools/telegram_send.py >> logs/cron.log 2>&1"

mkdir -p "$PROJECT_DIR/logs"

# Install crons (idempotent)
(crontab -l 2>/dev/null | grep -v "sports-agent\|fetch_fixtures\|telegram_send"; echo "$CRON_DATA"; echo "$CRON_SEND") | crontab -

echo "=== 5. Flask dashboard (systemd) ==="
cat > /etc/systemd/system/sports-dashboard.service << EOF
[Unit]
Description=Sports Agent Dashboard
After=network.target

[Service]
User=root
WorkingDirectory=$PROJECT_DIR/dashboard
ExecStart=$PROJECT_DIR/venv/bin/python app.py
EnvironmentFile=$PROJECT_DIR/.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sports-dashboard
systemctl restart sports-dashboard

echo ""
echo "=== DONE ==="
echo "Crontab:"
crontab -l
echo ""
echo "Dashboard: http://$(curl -s ifconfig.me):8000"
echo ""
echo "NEXT: copy .env to $PROJECT_DIR/.env"
echo "      rsync -av 'Sports Agent/' root@SERVER:$PROJECT_DIR/"
