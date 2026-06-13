"""Fetch WC top scorers + squad data for upcoming teams."""
import json, sqlite3
from pathlib import Path
from dotenv import load_dotenv
import http_client as hc

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"
CACHE_DIR = Path(__file__).parent.parent / ".tmp"


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            player_name TEXT,
            team_id INTEGER,
            team TEXT,
            stat_date TEXT,
            goals_total INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            penalties INTEGER DEFAULT 0,
            goals_per90 REAL DEFAULT 0,
            minutes_played INTEGER DEFAULT 0,
            appearances INTEGER DEFAULT 0,
            cards_yellow INTEGER DEFAULT 0,
            cards_red INTEGER DEFAULT 0,
            fetched_at TEXT,
            UNIQUE(player_id, stat_date)
        )
    """)
    conn.commit()


def get_upcoming_team_ids(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute("""
        SELECT DISTINCT home_team_id, home_team FROM matches
        WHERE status IN ('SCHEDULED','TIMED') AND date(kickoff_utc) <= date('now','+7 days')
        UNION
        SELECT DISTINCT away_team_id, away_team FROM matches
        WHERE status IN ('SCHEDULED','TIMED') AND date(kickoff_utc) <= date('now','+7 days')
    """).fetchall()


def fetch_wc_scorers() -> list:
    from datetime import date
    today = date.today().isoformat()
    cp = CACHE_DIR / today / "wc_scorers.json"
    cp.parent.mkdir(parents=True, exist_ok=True)

    if cp.exists():
        with open(cp) as f:
            return json.load(f)

    data = hc.fd_get("competitions/WC/scorers", {"limit": 50})
    scorers = data.get("scorers", [])
    with open(cp, "w") as f:
        json.dump(scorers, f)
    return scorers


def fetch_team_squad(team_id: int) -> dict:
    from datetime import date
    today = date.today().isoformat()
    cp = CACHE_DIR / today / f"squad_{team_id}.json"
    cp.parent.mkdir(parents=True, exist_ok=True)

    if cp.exists():
        with open(cp) as f:
            return json.load(f)

    data = hc.fd_get(f"teams/{team_id}")
    with open(cp, "w") as f:
        json.dump(data, f)
    return data


def upsert_scorer(conn: sqlite3.Connection, scorer: dict, today: str):
    p = scorer.get("player", {})
    t = scorer.get("team", {})
    goals = scorer.get("goals", 0) or 0
    assists = scorer.get("assists", 0) or 0
    penalties = scorer.get("penalties", 0) or 0
    played = scorer.get("playedMatches", 1) or 1
    g90 = round(goals / played, 3)

    conn.execute("""
        INSERT INTO player_stats
            (player_id, player_name, team_id, team, stat_date,
             goals_total, assists, penalties, goals_per90,
             cards_yellow, cards_red, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,0,0,datetime('now'))
        ON CONFLICT(player_id, stat_date) DO UPDATE SET
            goals_total=excluded.goals_total,
            assists=excluded.assists,
            penalties=excluded.penalties,
            goals_per90=excluded.goals_per90,
            fetched_at=excluded.fetched_at
    """, (
        p.get("id"), p.get("name"), t.get("id"), t.get("name"),
        today, goals, assists, penalties, g90,
    ))


def main():
    from datetime import date
    today = date.today().isoformat()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # WC top scorers — most reliable source
    try:
        scorers = fetch_wc_scorers()
        for s in scorers:
            upsert_scorer(conn, s, today)
        print(f"Loaded {len(scorers)} WC scorers")
    except Exception as e:
        print(f"ERROR fetching scorers: {e}")

    # Squad list for upcoming teams (position data, penalty takers inferred from penalties stat)
    teams = get_upcoming_team_ids(conn)
    print(f"Loading squads for {len(teams)} teams...")
    for team_id, team_name in teams:
        try:
            squad_data = fetch_team_squad(team_id)
            squad = squad_data.get("squad", [])
            # Upsert squad members not already in player_stats (goals=0 placeholder)
            for p in squad:
                conn.execute("""
                    INSERT OR IGNORE INTO player_stats
                        (player_id, player_name, team_id, team, stat_date, fetched_at)
                    VALUES (?,?,?,?,?,datetime('now'))
                """, (p.get("id"), p.get("name"), team_id, team_name, today))
            print(f"  OK: {team_name} ({len(squad)} players)")
        except Exception as e:
            print(f"  ERROR {team_name}: {e}")

    conn.commit()
    conn.close()
    print("Player stats upserted OK")


if __name__ == "__main__":
    main()
