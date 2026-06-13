"""
Fetch real team stats using /teams/statistics endpoint.
2 API calls per UNKNOWN team (search + stats), 1 call for KNOWN teams (stats only).
Team ID mapping cached permanently — search only runs once per team ever.
Daily stats cache resets each day.
Rate limit: 7s sleep, 100 req/day free tier.
"""
import json, sqlite3, sys, time
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
import http_client as hc

load_dotenv()

DB_PATH   = Path(__file__).parent.parent / "database" / "sports_agent.db"
CACHE_DIR = Path(__file__).parent.parent / ".tmp"
ID_CACHE  = CACHE_DIR / "team_id_map"   # persistent — never date-prefixed
TODAY     = date.today().isoformat()
SLEEP     = 7  # seconds per call

# Try leagues in order, stop when stats found (most teams need only 1-2 tries)
PRIORITY_LEAGUES = [
    (9,   2024),   # Copa America (South America + CONCACAF guests)
    (5,   2024),   # UEFA EURO 2024
    (6,   2024),   # UEFA Nations League
    (13,  2024),   # AFCON
    (17,  2024),   # AFC Asian Cup
    (22,  2025),   # CONCACAF Gold Cup
    (536, 2024),   # CONCACAF Nations League
    (13,  2023),   # AFCON qualifiers
    (10,  2024),   # Friendlies 2024
    (10,  2023),   # Friendlies 2023
]
MIN_GAMES = 1  # lowered from 5 — corners/cards proxy only needs SOME real games,
               # not a big sample. Faster coverage of all 48 teams (still real data).


# ── DB ────────────────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER, team TEXT, stat_date TEXT,
            avg_corners_for REAL DEFAULT 0,
            avg_corners_against REAL DEFAULT 0,
            avg_cards REAL DEFAULT 0,
            goals_1h REAL DEFAULT 0,
            goals_2h REAL DEFAULT 0,
            goals_scored_avg REAL DEFAULT 0,
            goals_conceded_avg REAL DEFAULT 0,
            form_last10 TEXT DEFAULT '[]',
            league_id INTEGER DEFAULT 0,
            fetched_at TEXT,
            UNIQUE(team_id, stat_date)
        )
    """)
    # Add league_id to existing tables that were created before this column existed
    try:
        conn.execute("ALTER TABLE team_stats ADD COLUMN league_id INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.commit()


def get_all_wc_teams(conn):
    rows = conn.execute("""
        SELECT DISTINCT home_team_id, home_team FROM matches
        UNION SELECT DISTINCT away_team_id, away_team FROM matches
        ORDER BY home_team
    """).fetchall()
    return [(tid, name) for tid, name in rows if tid and name]


def get_done_today(conn) -> set:
    rows = conn.execute("""
        SELECT team_id FROM team_stats
        WHERE stat_date=? AND form_last10 != '[]'
    """, (TODAY,)).fetchall()
    return {r[0] for r in rows}


def purge_zero_rows(conn):
    conn.execute(
        "DELETE FROM team_stats WHERE stat_date=? AND form_last10='[]'",
        (TODAY,)
    )
    conn.commit()


# ── API helpers ───────────────────────────────────────────────────────────────

def is_limit_error(data: dict) -> bool:
    """True if API returned daily-limit exhausted message."""
    errs = data.get("errors", {})
    if isinstance(errs, dict):
        return any("limit" in str(v).lower() or "reached" in str(v).lower()
                   for v in errs.values())
    return False


def get_as_id(fd_id: int, name: str) -> int | None:
    """Return api-sports.io team ID. Searches once, caches permanently."""
    ID_CACHE.mkdir(parents=True, exist_ok=True)
    cp = ID_CACHE / f"{fd_id}.json"
    if cp.exists():
        return json.load(open(cp)).get("id")

    # Try full name, then first word
    for query in [name, name.split()[0]]:
        time.sleep(SLEEP)
        data = hc.as_get("teams", {"search": query})
        if is_limit_error(data):
            print("  DAILY LIMIT HIT — aborting (run again tomorrow)")
            return "LIMIT"
        results = data.get("response", [])
        if results:
            nationals = [r for r in results if r["team"].get("national")]
            pick = nationals[0] if nationals else results[0]
            as_id = pick["team"]["id"]
            json.dump({"id": as_id, "name": pick["team"]["name"]}, open(cp, "w"))
            return as_id

    return None


def fetch_stats(as_id: int) -> tuple[dict, int] | None:
    """Fetch /teams/statistics for first matching league.
    Returns (resp_dict, league_id) or None. Caches which league won."""
    # Check if we already know the winning league for this team
    winner_cache = ID_CACHE / f"league_{as_id}.json"
    if winner_cache.exists():
        meta = json.load(open(winner_cache))
        leagues_to_try = [(meta["league_id"], meta["season"])]
    else:
        leagues_to_try = PRIORITY_LEAGUES

    for league_id, season in leagues_to_try:
        cp = CACHE_DIR / TODAY / f"as_stats_{as_id}_{league_id}_{season}.json"
        cp.parent.mkdir(parents=True, exist_ok=True)

        if cp.exists():
            cached = json.load(open(cp))
            if cached:
                return cached, league_id
            continue

        time.sleep(SLEEP)
        data = hc.as_get("teams/statistics", {
            "team": as_id, "league": league_id, "season": season,
        })

        if is_limit_error(data):
            print("  DAILY LIMIT HIT — aborting")
            return "LIMIT"

        resp = data.get("response") or {}
        if not isinstance(resp, dict):
            json.dump({}, open(cp, "w"))
            continue
        played = (resp.get("fixtures", {}).get("played", {}).get("total") or 0)

        if played >= MIN_GAMES:
            json.dump(resp, open(cp, "w"))
            # Cache winning league so next day = 1 call instead of up to 10
            json.dump({"league_id": league_id, "season": season}, open(winner_cache, "w"))
            return resp, league_id

        json.dump({}, open(cp, "w"))

    return None


# ── Parsing ───────────────────────────────────────────────────────────────────

def _n(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_minute_goals(minute_dict: dict, half: str) -> float:
    """Sum goals in a half from minute-breakdown dict."""
    if half == "1h":
        buckets = ["0-15", "16-30", "31-45"]
    else:
        buckets = ["46-60", "61-75", "76-90", "91-105"]
    return sum(_n((minute_dict.get(b) or {}).get("total")) for b in buckets)


def parse_yellow_cards(cards_dict: dict, games: int) -> float:
    """Total yellow cards per game from minute-breakdown dict."""
    total = sum(
        _n((v or {}).get("total")) for v in (cards_dict.get("yellow") or {}).values()
    )
    return round(total / max(games, 1), 2)


def analyse(resp: dict) -> dict:
    fx     = resp.get("fixtures", {})
    played = _n(fx.get("played", {}).get("total"))
    wins   = _n(fx.get("wins",   {}).get("total"))
    draws  = _n(fx.get("draws",  {}).get("total"))
    losses = _n(fx.get("loses",  {}).get("total"))

    goals_for     = resp.get("goals", {}).get("for",     {})
    goals_against = resp.get("goals", {}).get("against", {})

    gf_avg = _n(goals_for.get("average", {}).get("total"))
    ga_avg = _n(goals_against.get("average", {}).get("total"))

    gf_min = goals_for.get("minute", {})
    g1h = round(parse_minute_goals(gf_min, "1h") / max(played, 1), 2)
    g2h = round(parse_minute_goals(gf_min, "2h") / max(played, 1), 2)

    avg_cards = parse_yellow_cards(resp.get("cards", {}), int(played))

    # Build pseudo form from win/draw/loss counts (most recent unknown so use totals)
    form = (["W"] * int(wins) + ["D"] * int(draws) + ["L"] * int(losses))[-10:]

    return {
        "goals_scored_avg":    round(gf_avg, 2),
        "goals_conceded_avg":  round(ga_avg, 2),
        "goals_1h":            g1h,
        "goals_2h":            g2h,
        "avg_cards":           avg_cards,
        "avg_corners_for":     0.0,   # added by enrich_match_stats.py
        "avg_corners_against": 0.0,
        "form_last10":         json.dumps(form),
    }


# ── DB write ──────────────────────────────────────────────────────────────────

def upsert(conn, team_id, name, stats, league_id: int = 0):
    conn.execute("""
        INSERT INTO team_stats
            (team_id, team, stat_date, avg_corners_for, avg_corners_against,
             avg_cards, goals_1h, goals_2h, goals_scored_avg, goals_conceded_avg,
             form_last10, league_id, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(team_id, stat_date) DO UPDATE SET
            goals_scored_avg=excluded.goals_scored_avg,
            goals_conceded_avg=excluded.goals_conceded_avg,
            goals_1h=excluded.goals_1h,
            goals_2h=excluded.goals_2h,
            avg_cards=excluded.avg_cards,
            form_last10=excluded.form_last10,
            league_id=excluded.league_id,
            fetched_at=excluded.fetched_at
    """, (
        team_id, name, TODAY,
        stats["avg_corners_for"], stats["avg_corners_against"],
        stats["avg_cards"], stats["goals_1h"], stats["goals_2h"],
        stats["goals_scored_avg"], stats["goals_conceded_avg"],
        stats["form_last10"], league_id,
    ))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    purge_zero_rows(conn)

    all_teams = get_all_wc_teams(conn)
    done      = get_done_today(conn)
    teams     = [(tid, name) for tid, name in all_teams if tid not in done]
    known_ids = len([f for f in ID_CACHE.glob("*.json")]) if ID_CACHE.exists() else 0

    print(f"Teams cached: {known_ids}/48  |  Already done today: {len(done)}")
    print(f"To fetch: {len(teams)} teams\n")

    ok = skip = err = 0
    for fd_id, name in teams:
        try:
            as_id = get_as_id(fd_id, name)
            if as_id == "LIMIT":
                conn.commit()
                conn.close()
                print(f"\nDone (daily limit). OK={ok}  SKIP={skip}  ERR={err}")
                sys.exit(0)
            if not as_id:
                print(f"  SKIP {name}: not found in api-sports.io")
                skip += 1
                continue

            result = fetch_stats(as_id)
            if result == "LIMIT":
                conn.commit()
                conn.close()
                print(f"\nDone (daily limit). OK={ok}  SKIP={skip}  ERR={err}")
                sys.exit(0)
            if not result:
                print(f"  SKIP {name}: no stats in any league (≥{MIN_GAMES} games)")
                skip += 1
                continue

            resp, league_id = result
            stats = analyse(resp)
            upsert(conn, fd_id, name, stats, league_id)
            conn.commit()  # commit immediately — sys.exit on limit would otherwise lose data

            form = "".join(json.loads(stats["form_last10"]))
            print(f"  OK: {name:25s} | form={form:10s} | "
                  f"gf={stats['goals_scored_avg']:.2f} ga={stats['goals_conceded_avg']:.2f} "
                  f"cards={stats['avg_cards']:.1f}")
            ok += 1

        except Exception as e:
            print(f"  ERROR {name}: {e}")
            err += 1

    conn.commit()
    conn.close()
    print(f"\nDone. OK={ok}  SKIP={skip}  ERR={err}")
    if ok:
        print("Next: python enrich_match_stats.py  →  python scoring_model.py")


if __name__ == "__main__":
    main()
