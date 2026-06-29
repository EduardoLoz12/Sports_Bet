"""Fetch WC 2026 fixtures for next 48h and upsert into matches table."""
import json, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
import http_client as hc

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"
CACHE_DIR = Path(__file__).parent.parent / ".tmp"


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            home_team_id INTEGER,
            away_team_id INTEGER,
            kickoff_utc TEXT,
            group_stage TEXT,
            stage TEXT,
            status TEXT DEFAULT 'SCHEDULED',
            home_score INTEGER,
            away_score INTEGER
        )
    """)
    # Migrate existing tables that lack score columns
    existing = {r[1] for r in conn.execute("PRAGMA table_info(matches)").fetchall()}
    if "home_score" not in existing:
        conn.execute("ALTER TABLE matches ADD COLUMN home_score INTEGER")
    if "away_score" not in existing:
        conn.execute("ALTER TABLE matches ADD COLUMN away_score INTEGER")
    conn.commit()


def cache_path(date_str: str) -> Path:
    d = CACHE_DIR / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d / "fixtures.json"


def fetch_fixtures_window() -> list:
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    # Look back 2 days too, so matches that kicked off recently get their
    # status updated to FINISHED (a forward-only window leaves them stuck
    # at TIMED forever, and predict_wc2026.py keeps re-predicting them).
    date_from = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=2)).strftime("%Y-%m-%d")

    cp = cache_path(today_str)
    if cp.exists():
        with open(cp) as f:
            return json.load(f)

    try:
        data = hc.fd_get("competitions/WC/matches", {
            "dateFrom": date_from,
            "dateTo": date_to,
        })
    except Exception as e:
        print(f"  WARNING: fixtures fetch failed ({e}) — skipping today, matches table unchanged")
        return []

    matches = data.get("matches", [])
    with open(cp, "w") as f:
        json.dump(matches, f)
    return matches


def fetch_all_wc_matches() -> list:
    """Fetch full WC schedule — used for initial DB population."""
    from datetime import date
    today = date.today().isoformat()
    cp = cache_path(today)
    full_cp = cp.parent / "all_matches.json"

    if full_cp.exists():
        with open(full_cp) as f:
            return json.load(f)

    data = hc.fd_get("competitions/WC/matches")
    matches = data.get("matches", [])
    with open(full_cp, "w") as f:
        json.dump(matches, f)
    return matches


def upsert_match(conn: sqlite3.Connection, m: dict):
    home = m["homeTeam"]
    away = m["awayTeam"]
    stage = m.get("stage", "GROUP_STAGE")
    group = m.get("group") or stage

    score = m.get("score", {})
    ft = score.get("fullTime", {})
    home_score = ft.get("home")
    away_score = ft.get("away")

    conn.execute("""
        INSERT INTO matches (match_id, home_team, away_team, home_team_id, away_team_id,
                             kickoff_utc, group_stage, stage, status, home_score, away_score)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(match_id) DO UPDATE SET
            status=excluded.status,
            kickoff_utc=excluded.kickoff_utc,
            home_score=excluded.home_score,
            away_score=excluded.away_score,
            home_team=COALESCE(excluded.home_team, matches.home_team),
            away_team=COALESCE(excluded.away_team, matches.away_team),
            home_team_id=COALESCE(excluded.home_team_id, matches.home_team_id),
            away_team_id=COALESCE(excluded.away_team_id, matches.away_team_id)
    """, (
        str(m["id"]),
        home.get("name", ""),
        away.get("name", ""),
        home.get("id"),
        away.get("id"),
        m.get("utcDate", ""),
        group,
        stage,
        m.get("status", "SCHEDULED"),
        home_score,
        away_score,
    ))


def backfill_missing_scores(conn: sqlite3.Connection) -> int:
    """Some matches get marked FINISHED by the +/-2 day window before football-data.org
    posts the final score, then age out of the window and stay NULL forever. Catch them
    with one full-schedule call (cached per day) and re-upsert just those match_ids."""
    missing = conn.execute("""
        SELECT match_id FROM matches
        WHERE status='FINISHED' AND (home_score IS NULL OR away_score IS NULL)
    """).fetchall()
    if not missing:
        return 0
    missing_ids = {r[0] for r in missing}
    print(f"  Backfilling scores for {len(missing_ids)} FINISHED match(es) with NULL score...")
    try:
        full = fetch_all_wc_matches()
    except Exception as e:
        print(f"  WARNING: backfill fetch failed ({e})")
        return 0
    n = 0
    for m in full:
        if str(m["id"]) in missing_ids:
            upsert_match(conn, m)
            n += 1
    return n


def main(all_matches: bool = False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if all_matches:
        matches = fetch_all_wc_matches()
        print(f"Loaded full WC schedule: {len(matches)} matches")
    else:
        matches = fetch_fixtures_window()
        print(f"Found {len(matches)} WC matches in next 48h")

    for m in matches:
        upsert_match(conn, m)
        home = m["homeTeam"].get("name", "?")
        away = m["awayTeam"].get("name", "?")
        print(f"  {home} vs {away} | {m.get('utcDate','')[:10]} | {m.get('status')}")

    n_backfilled = backfill_missing_scores(conn)
    if n_backfilled:
        print(f"  Backfilled {n_backfilled} score(s)")

    conn.commit()
    conn.close()
    print("Fixtures upserted OK")


if __name__ == "__main__":
    import sys
    main(all_matches="--all" in sys.argv)
