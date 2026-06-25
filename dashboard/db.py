"""DB access layer. Local/Hetzner: SQLite file. Vercel: read-only Supabase (Postgres) replica."""
import os
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"
# Only switch to Postgres on Vercel (VERCEL=1 is set automatically there).
# Hetzner's .env also holds SUPABASE_DB_URL (for sync_to_supabase.py's push
# target) but its own dashboard must keep reading local SQLite.
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL") if os.getenv("VERCEL") else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT, market TEXT, pick TEXT,
    confidence INTEGER, odds REAL, stake_tier TEXT,
    created_at TEXT,
    UNIQUE(match_id, market)
);
CREATE TABLE IF NOT EXISTS player_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER, player_name TEXT,
    team_id INTEGER, team TEXT, stat_date TEXT,
    goals_total INTEGER DEFAULT 0, assists INTEGER DEFAULT 0,
    penalties INTEGER DEFAULT 0, goals_per90 REAL DEFAULT 0,
    minutes_played INTEGER DEFAULT 0, appearances INTEGER DEFAULT 0,
    cards_yellow INTEGER DEFAULT 0, cards_red INTEGER DEFAULT 0,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS model_meta (
    key TEXT PRIMARY KEY,
    json TEXT
);
CREATE TABLE IF NOT EXISTS match_sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT UNIQUE,
    home_team TEXT, away_team TEXT,
    home_win_pct INTEGER, draw_pct INTEGER, away_win_pct INTEGER,
    summary TEXT, top_themes TEXT, post_count INTEGER, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS standings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team TEXT, team_id INTEGER, group_label TEXT,
    position INTEGER, played INTEGER DEFAULT 0,
    won INTEGER DEFAULT 0, draw INTEGER DEFAULT 0, lost INTEGER DEFAULT 0,
    gf INTEGER DEFAULT 0, ga INTEGER DEFAULT 0, gd INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0, updated_at TEXT,
    UNIQUE(team_id, group_label)
);
"""

_PLACEHOLDER_RE = re.compile(r"\?")


class _Row(dict):
    """dict subclass so both row["col"] and dict(row) work like sqlite3.Row."""


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [c.name for c in self._cur.description]
        return _Row(zip(cols, row))

    def fetchall(self):
        rows = self._cur.fetchall()
        cols = [c.name for c in self._cur.description]
        return [_Row(zip(cols, r)) for r in rows]


class _PgConn:
    """Thin wrapper giving psycopg2 the sqlite3 .execute/.fetchone/.fetchall shape."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(_PLACEHOLDER_RE.sub("%s", sql), tuple(params))
        return _PgCursor(cur)

    def close(self):
        self._conn.close()


def get_db():
    if SUPABASE_DB_URL:
        import psycopg2
        conn = psycopg2.connect(SUPABASE_DB_URL)
        return _PgConn(conn)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    if SUPABASE_DB_URL:
        return  # Supabase schema is managed by supabase/schema.sql + sync_to_supabase.py
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
