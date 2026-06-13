"""
Enrich corners + cards for teams playing in next 7 days.
Fetches /fixtures/statistics per recent fixture (costly — use sparingly).
Run once per matchday, not daily for all 48 teams.
Rate: 7s sleep, ~1 req per fixture stat call.
"""
import json, sqlite3, time
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
import http_client as hc

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"
CACHE_DIR = Path(__file__).parent.parent / ".tmp"
TODAY = date.today().isoformat()
SLEEP = 7

# Leagues to search for recent fixtures with stats
STAT_LEAGUES = [
    (9,   [2024]),       # Copa America
    (5,   [2024]),       # EURO 2024
    (6,   [2024]),       # UEFA NL
    (13,  [2024, 2023]), # AFCON
    (17,  [2024, 2023]), # AFC Asian Cup
    (22,  [2025, 2023]), # Gold Cup
    (536, [2024]),       # CONCACAF NL
    (10,  [2024]),       # Friendlies
]
FIXTURES_TO_ANALYSE = 8


def get_upcoming_teams(conn) -> list[tuple]:
    return conn.execute("""
        SELECT DISTINCT home_team_id, home_team FROM matches
        WHERE status IN ('SCHEDULED','TIMED') AND date(kickoff_utc) <= date('now','+7 days')
        UNION
        SELECT DISTINCT away_team_id, away_team FROM matches
        WHERE status IN ('SCHEDULED','TIMED') AND date(kickoff_utc) <= date('now','+7 days')
    """).fetchall()


def get_as_id(fd_id: int) -> int | None:
    cp = CACHE_DIR / TODAY / f"as_team_{fd_id}.json"
    if cp.exists():
        return json.load(open(cp)).get("id")
    return None


def fetch_recent_fixture_ids(as_id: int) -> list[int]:
    """Get IDs of recent finished fixtures for this team."""
    cp = CACHE_DIR / TODAY / f"as_fxs_{as_id}.json"
    if cp.exists():
        fxs = json.load(open(cp))
        return [fx["fixture"]["id"] for fx in fxs[-FIXTURES_TO_ANALYSE:]]

    # Fetch fresh if not cached
    collected = []
    for league_id, seasons in STAT_LEAGUES:
        if len(collected) >= FIXTURES_TO_ANALYSE:
            break
        for season in seasons:
            time.sleep(SLEEP)
            data = hc.as_get("fixtures", {
                "team": as_id, "league": league_id,
                "season": season, "status": "FT",
            })
            if data.get("errors"):
                continue
            fxs = data.get("response", [])
            collected.extend(fxs)
            if len(collected) >= FIXTURES_TO_ANALYSE:
                break

    json.dump(collected[:FIXTURES_TO_ANALYSE], open(cp, "w"))
    return [fx["fixture"]["id"] for fx in collected[:FIXTURES_TO_ANALYSE]]


def fetch_fixture_stats(fixture_id: int, as_id: int) -> dict:
    cp = CACHE_DIR / TODAY / f"as_fxstats_{fixture_id}_{as_id}.json"
    if cp.exists():
        return json.load(open(cp))

    time.sleep(SLEEP)
    data = hc.as_get("fixtures/statistics", {"fixture": fixture_id, "team": as_id})
    result = {}
    for team_data in data.get("response", []):
        if team_data.get("team", {}).get("id") == as_id:
            for item in team_data.get("statistics", []):
                result[item["type"]] = item["value"]
            break
    json.dump(result, open(cp, "w"))
    return result


def parse_num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def update_corners_cards(conn, fd_id: int, name: str, as_id: int, fixture_ids: list[int]):
    corners_total = cards_total = n = 0
    for fx_id in fixture_ids:
        stats = fetch_fixture_stats(fx_id, as_id)
        if stats:
            corners_total += parse_num(stats.get("Corner Kicks"))
            cards_total += parse_num(stats.get("Yellow Cards"))
            n += 1

    if n == 0:
        print(f"  SKIP {name}: no stats returned")
        return

    avg_corners = round(corners_total / n, 2)
    avg_cards = round(cards_total / n, 2)

    conn.execute("""
        UPDATE team_stats
        SET avg_corners_for=?, avg_cards=?, fetched_at=datetime('now')
        WHERE team_id=? AND stat_date=?
    """, (avg_corners, avg_cards, fd_id, TODAY))

    print(f"  OK: {name} | corners={avg_corners} | cards={avg_cards} (n={n})")


def main():
    conn = sqlite3.connect(DB_PATH)
    teams = get_upcoming_teams(conn)
    teams = [(tid, name) for tid, name in teams if tid and name]

    print(f"Enriching corners/cards for {len(teams)} upcoming teams...\n")

    for fd_id, name in teams:
        as_id = get_as_id(fd_id)
        if not as_id:
            print(f"  SKIP {name}: no api-sports.io ID (run fetch_team_stats.py first)")
            continue
        try:
            fx_ids = fetch_recent_fixture_ids(as_id)
            if not fx_ids:
                print(f"  SKIP {name}: no fixtures")
                continue
            update_corners_cards(conn, fd_id, name, as_id, fx_ids[:FIXTURES_TO_ANALYSE])
        except Exception as e:
            print(f"  ERROR {name}: {e}")

    conn.commit()
    conn.close()
    print("\nDone. team_stats corners/cards updated.")


if __name__ == "__main__":
    main()
