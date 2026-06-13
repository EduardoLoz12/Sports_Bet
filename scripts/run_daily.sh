#!/bin/bash
# Daily cron job — run on Hetzner at 06:00 AM Peru time (11:00 UTC)
# Crontab entry: 0 11 * * * /path/to/sports-agent/scripts/run_daily.sh
#
# No `set -e`: each step runs independently. If one step fails (API daily
# limit, transient SSL error, etc.) the rest of the chain still runs —
# train_model/predict_wc2026 always refresh with whatever data is available.

cd "$(dirname "$0")/.."

echo "[$(date)] Starting daily data fetch..."

run_step() {
    echo "--- $1 ---"
    python3 "$1"
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "    WARNING: $1 exited $rc — continuing"
    fi
}

run_step tools/fetch_fixtures.py
run_step tools/fetch_team_stats.py
run_step tools/fetch_player_stats.py
run_step tools/fetch_intl_stats.py
run_step tools/collect_training_data.py
run_step tools/train_model.py
run_step tools/predict_wc2026.py
run_step tools/sync_to_turso.py

echo "[$(date)] Data fetch + ML pipeline complete"
