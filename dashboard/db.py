"""DB access layer. Local/Hetzner: SQLite file. Vercel: read-only Turso (libSQL) replica."""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")


SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT, market TEXT, pick TEXT,
    odds REAL, stake_soles REAL,
    result TEXT DEFAULT 'pending',
    profit_soles REAL, placed_at DATETIME, settled_at DATETIME
);
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
"""


class _Row(dict):
    """dict subclass so both row["col"] and dict(row) work like sqlite3.Row."""


class _TursoCursor:
    def __init__(self, result_set):
        self._result_set = result_set

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def fetchall(self):
        cols = self._result_set.columns
        return [_Row(zip(cols, row)) for row in self._result_set.rows]


class _TursoConn:
    """Thin wrapper giving libsql_client the sqlite3 .execute/.fetchone/.fetchall shape."""

    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=()):
        return _TursoCursor(self._client.execute(sql, params))

    def close(self):
        self._client.close()


def get_db():
    if TURSO_URL:
        import libsql_client
        client = libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)
        return _TursoConn(client)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    if TURSO_URL:
        return  # Turso replica is schema-managed by sync_to_turso.py
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
