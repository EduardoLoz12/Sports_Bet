"""Push a read-only snapshot of sports_agent.db to Turso for the Vercel dashboard.

Run on Hetzner at the end of run_daily.sh (and after log_bet.py). Mirrors only
the tables the dashboard reads — training_matches/dc_team_index stay local.
Requires TURSO_DATABASE_URL + TURSO_AUTH_TOKEN (write token) in .env.
"""
import json
import os
import sqlite3
from pathlib import Path

import libsql_client
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "sports_agent.db"
MODELS_DIR = ROOT / "models"

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

TABLES = ["matches", "predictions", "bets", "team_stats", "team_extended_stats", "player_stats"]

MODEL_META_SCHEMA = "CREATE TABLE IF NOT EXISTS model_meta (key TEXT PRIMARY KEY, json TEXT)"


def main():
    if not TURSO_URL or not TURSO_TOKEN:
        print("TURSO_DATABASE_URL / TURSO_AUTH_TOKEN not set — skipping sync")
        return

    local = sqlite3.connect(DB_PATH)
    local.row_factory = sqlite3.Row
    remote = libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)

    for table in TABLES:
        row = local.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not row:
            print(f"  skip {table}: not found locally")
            continue

        create_sql = row["sql"].replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
        remote.execute(create_sql)
        remote.execute(f"DELETE FROM {table}")

        rows = local.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: 0 rows")
            continue

        cols = rows[0].keys()
        placeholders = ", ".join("?" for _ in cols)
        insert_sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        for r in rows:
            remote.execute(insert_sql, tuple(r[c] for c in cols))
        print(f"  {table}: {len(rows)} rows")

    # Model metadata (dc_params.json / eval_report.json) for /api/model_info
    remote.execute(MODEL_META_SCHEMA)
    for key, fname in [("dc_params", "dc_params.json"), ("eval_report", "eval_report.json")]:
        f = MODELS_DIR / fname
        if f.exists():
            remote.execute(
                "INSERT OR REPLACE INTO model_meta (key, json) VALUES (?, ?)",
                (key, f.read_text(encoding="utf-8")),
            )
            print(f"  model_meta.{key}: synced")

    remote.close()
    local.close()
    print("Sync to Turso complete")


if __name__ == "__main__":
    main()
