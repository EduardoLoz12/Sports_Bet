"""Push a read-only snapshot of sports_agent.db to Supabase for the Vercel dashboard.

Run on Hetzner at the end of run_daily.sh (and after log_bet.py). Mirrors only
the tables dashboard/app.py reads — training_matches/dc_team_index stay local.
Requires SUPABASE_DB_URL (direct Postgres connection string, see
supabase/schema.sql) in .env. Full TRUNCATE + re-INSERT each run.
"""
import json
import os
import sqlite3
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "sports_agent.db"
MODELS_DIR = ROOT / "models"

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")

TABLE_COLUMNS = {
    "matches": [
        "match_id", "home_team", "away_team", "home_team_id", "away_team_id",
        "kickoff_utc", "group_stage", "stage", "status",
    ],
    "predictions": [
        "match_id", "market", "pick", "confidence", "odds", "stake_tier", "created_at",
    ],
    "bets": [
        "match_id", "market", "pick", "odds", "stake_soles", "result",
        "profit_soles", "placed_at", "settled_at",
    ],
    "team_stats": [
        "team_id", "team", "stat_date", "avg_corners_for", "avg_corners_against",
        "avg_cards", "goals_1h", "goals_2h", "goals_scored_avg", "goals_conceded_avg",
        "form_last10", "league_id", "fetched_at",
    ],
    "team_extended_stats": [
        "team", "fifa_rank", "elo_approx", "wc_titles", "continental_titles",
        "confederation", "total_matches", "win_pct", "draw_pct", "loss_pct",
        "gf_avg", "ga_avg", "goal_diff_avg", "comp_gf_avg", "comp_ga_avg",
        "comp_win_pct", "clean_sheet_pct", "big_win_pct", "big_loss_pct",
        "form5", "form10", "pts_pct_5", "pts_pct_10", "pts_pct_20",
        "last_match", "updated_at",
    ],
    "player_stats": [
        "player_id", "player_name", "team_id", "team", "stat_date",
        "goals_total", "assists", "penalties", "goals_per90",
        "minutes_played", "appearances", "cards_yellow", "cards_red", "fetched_at",
    ],
}


def sync_table(local, remote_cur, table, columns):
    local_row = local.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not local_row:
        print(f"  skip {table}: not found locally")
        return

    rows = local.execute(f"SELECT * FROM {table}").fetchall()
    remote_cur.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")

    if not rows:
        print(f"  {table}: 0 rows")
        return

    local_cols = set(rows[0].keys())
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
    data = [tuple(r[c] if c in local_cols else None for c in columns) for r in rows]
    remote_cur.executemany(insert_sql, data)
    print(f"  {table}: {len(rows)} rows")


def main():
    if not SUPABASE_DB_URL:
        print("SUPABASE_DB_URL not set — skipping sync")
        return

    local = sqlite3.connect(DB_PATH)
    local.row_factory = sqlite3.Row
    remote = psycopg2.connect(SUPABASE_DB_URL)
    cur = remote.cursor()

    for table, columns in TABLE_COLUMNS.items():
        sync_table(local, cur, table, columns)

    # Model metadata (dc_params.json / eval_report.json) for /api/model_info
    for key, fname in [("dc_params", "dc_params.json"), ("eval_report", "eval_report.json")]:
        f = MODELS_DIR / fname
        if f.exists():
            cur.execute(
                """
                INSERT INTO model_meta (key, json) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET json = EXCLUDED.json
                """,
                (key, f.read_text(encoding="utf-8")),
            )
            print(f"  model_meta.{key}: synced")

    remote.commit()
    cur.close()
    remote.close()
    local.close()
    print("Sync to Supabase complete")


if __name__ == "__main__":
    main()
