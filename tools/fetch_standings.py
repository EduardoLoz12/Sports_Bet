"""Fetch WC 2026 group standings from football-data.org → standings table.

One call to competitions/WC/standings. Cached per day under .tmp/<date>/standings.json.
Feeds the dashboard's group-table panel (position, points, GD, points-to-qualify estimate).
"""
import json, sqlite3
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
import http_client as hc

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"
CACHE_DIR = Path(__file__).parent.parent / ".tmp"


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS standings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT,
            team_id INTEGER,
            group_label TEXT,
            position INTEGER,
            played INTEGER DEFAULT 0,
            won INTEGER DEFAULT 0,
            draw INTEGER DEFAULT 0,
            lost INTEGER DEFAULT 0,
            gf INTEGER DEFAULT 0,
            ga INTEGER DEFAULT 0,
            gd INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0,
            updated_at TEXT,
            UNIQUE(team_id, group_label)
        )
    """)
    conn.commit()


def fetch_standings() -> list:
    today = date.today().isoformat()
    cp = CACHE_DIR / today / "standings.json"
    cp.parent.mkdir(parents=True, exist_ok=True)

    if cp.exists():
        with open(cp) as f:
            return json.load(f)

    data = hc.fd_get("competitions/WC/standings")
    standings = data.get("standings", [])
    with open(cp, "w") as f:
        json.dump(standings, f)
    return standings


def upsert_row(conn, row: dict, group_label: str, today: str):
    t = row.get("team", {})
    conn.execute("""
        INSERT INTO standings
            (team, team_id, group_label, position, played, won, draw, lost,
             gf, ga, gd, points, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(team_id, group_label) DO UPDATE SET
            team=excluded.team,
            position=excluded.position,
            played=excluded.played,
            won=excluded.won,
            draw=excluded.draw,
            lost=excluded.lost,
            gf=excluded.gf,
            ga=excluded.ga,
            gd=excluded.gd,
            points=excluded.points,
            updated_at=excluded.updated_at
    """, (
        t.get("name", ""),
        t.get("id"),
        group_label,
        row.get("position"),
        row.get("playedGames", 0) or 0,
        row.get("won", 0) or 0,
        row.get("draw", 0) or 0,
        row.get("lost", 0) or 0,
        row.get("goalsFor", 0) or 0,
        row.get("goalsAgainst", 0) or 0,
        row.get("goalDifference", 0) or 0,
        row.get("points", 0) or 0,
        today,
    ))


def main():
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    try:
        standings = fetch_standings()
    except Exception as e:
        print(f"ERROR fetching standings: {e}")
        conn.close()
        return

    n = 0
    for block in standings:
        # football-data.org returns one block per group; "group" holds the label
        # (e.g. "GROUP_A"). Skip non-TOTAL types if present.
        if block.get("type") and block.get("type") != "TOTAL":
            continue
        group_label = (block.get("group") or "").replace("GROUP_", "Grupo ").strip() or "—"
        for row in block.get("table", []):
            upsert_row(conn, row, group_label, today)
            n += 1
        print(f"  {group_label}: {len(block.get('table', []))} teams")

    conn.commit()
    conn.close()
    print(f"Standings upserted OK ({n} rows)")


if __name__ == "__main__":
    main()
