"""Weighted scoring model — generates predictions for all upcoming matches."""
import json, sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"

# ── Weight vectors (tune after each WC match) ─────────────────────────────────
W_WINNER = {
    "form": 0.30,
    "goals_scored": 0.25,
    "goals_conceded": 0.25,
    "head_to_head": 0.20,  # placeholder — H2H not yet in DB
}
W_CORNERS = {
    "corners_for": 0.45,
    "corners_against": 0.35,
    "possession_proxy": 0.20,  # using goals_scored as proxy
}
W_CARDS = {
    "team_cards": 0.50,
    "opponent_cards": 0.30,
    "intensity_proxy": 0.20,  # derby/group stage proxy
}
W_SCORER = {
    "goals_per90": 0.55,
    "minutes": 0.20,
    "penalty": 0.15,
    "form": 0.10,
}

# ── League quality multipliers ────────────────────────────────────────────────
# Applied to form + attack/defense scores. A 10-W streak in AFC qualifiers
# (vs Kyrgyzstan, Hong Kong) is NOT equivalent to 10 wins in UEFA/Copa America.
LEAGUE_QUALITY = {
    5:   1.00,  # UEFA EURO
    6:   0.95,  # UEFA Nations League
    9:   0.90,  # Copa America (CONMEBOL)
    22:  0.70,  # CONCACAF Gold Cup
    536: 0.68,  # CONCACAF Nations League
    13:  0.65,  # AFCON
    17:  0.60,  # AFC Asian Cup
    10:  0.50,  # Friendlies
    0:   0.75,  # Unknown / not yet stored
}

LEAGUE_LABEL = {
    5: "UEFA EURO", 6: "UEFA NL", 9: "Copa America",
    22: "CONCACAF GC", 536: "CONCACAF NL", 13: "AFCON",
    17: "AFC Cup", 10: "Friendlies", 0: "Desconocida",
}

# Thresholds
THRESHOLD_HIGH = 70
THRESHOLD_MED = 50

# Default lines (update once Betano lines are available)
DEFAULT_CORNERS_LINE = 9.5
DEFAULT_CARDS_LINE = 3.5


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            market TEXT,
            pick TEXT,
            confidence INTEGER,
            odds REAL,
            stake_tier TEXT,
            created_at TEXT,
            UNIQUE(match_id, market, pick)
        )
    """)
    conn.commit()


def get_team_stats(conn: sqlite3.Connection, team_id: int) -> dict:
    row = conn.execute("""
        SELECT * FROM team_stats WHERE team_id=? ORDER BY stat_date DESC LIMIT 1
    """, (team_id,)).fetchone()
    if not row:
        return {}
    cols = [d[0] for d in conn.execute("SELECT * FROM team_stats LIMIT 0").description]
    return dict(zip(cols, row))


def form_score(form_json: str) -> float:
    """Convert W/D/L string to 0-1 score."""
    try:
        results = json.loads(form_json or "[]")
    except Exception:
        return 0.5
    if not results:
        return 0.5
    pts = sum(3 if r == "W" else 1 if r == "D" else 0 for r in results)
    return pts / (len(results) * 3)


def confidence(score: float) -> int:
    return max(0, min(100, int(score * 100)))


def tier(conf: int) -> str:
    if conf >= THRESHOLD_HIGH:
        return "HIGH"
    if conf >= THRESHOLD_MED:
        return "MED"
    return "LOW"


def predict_winner(home_stats: dict, away_stats: dict) -> tuple[str, int]:
    """Returns (pick, confidence). Applies league quality multiplier so
    a 10-W streak in AFC qualifiers doesn't outrank UEFA wins."""
    hq = LEAGUE_QUALITY.get(home_stats.get("league_id", 0), 0.75)
    aq = LEAGUE_QUALITY.get(away_stats.get("league_id", 0), 0.75)

    home_form = form_score(home_stats.get("form_last10", "[]")) * hq
    away_form = form_score(away_stats.get("form_last10", "[]")) * aq

    home_attack  = min(home_stats.get("goals_scored_avg",  1.0) / 3.0, 1.0) * hq
    away_attack  = min(away_stats.get("goals_scored_avg",  1.0) / 3.0, 1.0) * aq
    home_defense = (1.0 - min(home_stats.get("goals_conceded_avg", 1.5) / 3.0, 1.0)) * hq
    away_defense = (1.0 - min(away_stats.get("goals_conceded_avg", 1.5) / 3.0, 1.0)) * aq

    home_score = (home_form    * W_WINNER["form"] +
                  home_attack  * W_WINNER["goals_scored"] +
                  home_defense * W_WINNER["goals_conceded"] +
                  0.5          * W_WINNER["head_to_head"])

    away_score = (away_form    * W_WINNER["form"] +
                  away_attack  * W_WINNER["goals_scored"] +
                  away_defense * W_WINNER["goals_conceded"] +
                  0.5          * W_WINNER["head_to_head"])

    diff = home_score - away_score
    if abs(diff) < 0.04:
        pick = "draw"
        conf = confidence(0.5 + abs(diff))
    elif diff > 0:
        pick = "home"
        conf = confidence(0.5 + diff * 2)
    else:
        pick = "away"
        conf = confidence(0.5 + abs(diff) * 2)

    return pick, conf


def predict_corners(home_stats: dict, away_stats: dict) -> tuple[str, int]:
    """Returns ('over X.X' or 'under X.X', confidence)."""
    home_for = home_stats.get("avg_corners_for", 5.0)
    home_against = home_stats.get("avg_corners_against", 5.0)
    away_for = away_stats.get("avg_corners_for", 5.0)
    away_against = away_stats.get("avg_corners_against", 5.0)

    # Use goals_scored as possession proxy when corners not available
    if home_for == 0.0:
        home_for = home_stats.get("goals_scored_avg", 1.0) * 3.5
    if away_for == 0.0:
        away_for = away_stats.get("goals_scored_avg", 1.0) * 3.5

    expected_corners = (home_for + away_against + away_for + home_against) / 2
    line = DEFAULT_CORNERS_LINE

    if expected_corners > line + 1:
        pick = f"over {line}"
        conf = confidence(min((expected_corners - line) / 3, 1.0))
    elif expected_corners < line - 1:
        pick = f"under {line}"
        conf = confidence(min((line - expected_corners) / 3, 1.0))
    else:
        pick = f"over {line}"
        conf = confidence(0.45)

    return pick, conf


def predict_cards(home_stats: dict, away_stats: dict) -> tuple[str, int]:
    """Returns ('over X.X' or 'under X.X', confidence).
    Returns LOW confidence when card data is missing (both = 0)."""
    home_cards = home_stats.get("avg_cards", 0.0)
    away_cards = away_stats.get("avg_cards", 0.0)
    line = DEFAULT_CARDS_LINE

    # No card data available — don't give false HIGH confidence
    if home_cards == 0.0 and away_cards == 0.0:
        return f"bajo {line} (sin datos)", 35

    expected = (home_cards or 2.0) + (away_cards or 2.0)

    if expected > line + 0.5:
        pick = f"sobre {line}"
        conf = confidence(min((expected - line) / 2, 1.0))
    elif expected < line - 0.5:
        pick = f"bajo {line}"
        conf = confidence(min((line - expected) / 2, 1.0))
    else:
        pick = f"sobre {line}"
        conf = confidence(0.45)

    return pick, conf


def predict_top_scorer(conn: sqlite3.Connection, team_id: int) -> tuple[str, int]:
    """Returns (player_name, confidence)."""
    players = conn.execute("""
        SELECT player_name, goals_per90, minutes_played, penalties
        FROM player_stats WHERE team_id=? AND minutes_played > 0
        ORDER BY goals_per90 DESC LIMIT 5
    """, (team_id,)).fetchall()

    if not players:
        return ("Unknown", 30)

    top = players[0]
    name, g90, mins, pens = top
    raw = (g90 / 1.0) * W_SCORER["goals_per90"]
    raw += min(mins / 900, 1.0) * W_SCORER["minutes"]
    raw += (1.0 if (pens or 0) > 0 else 0.0) * W_SCORER["penalty"]
    conf = confidence(min(raw * 1.5, 1.0))
    return name, conf


def upsert_prediction(conn: sqlite3.Connection, match_id: str, market: str,
                      pick: str, conf: int, odds: float = 0.0):
    conn.execute("""
        INSERT INTO predictions (match_id, market, pick, confidence, odds, stake_tier, created_at)
        VALUES (?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(match_id, market, pick) DO UPDATE SET
            confidence=excluded.confidence,
            stake_tier=excluded.stake_tier,
            created_at=excluded.created_at
    """, (match_id, market, pick, conf, odds, tier(conf)))


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    matches = conn.execute("""
        SELECT match_id, home_team, away_team, home_team_id, away_team_id, kickoff_utc
        FROM matches WHERE status IN ('SCHEDULED','TIMED') AND date(kickoff_utc) <= date('now', '+30 days')
        ORDER BY kickoff_utc
    """).fetchall()

    if not matches:
        print("No upcoming matches in DB — run fetch_fixtures.py first")
        conn.close()
        return

    for match_id, home, away, home_id, away_id, kickoff in matches:
        print(f"\n{home} vs {away} | {kickoff[:10]}")

        hs  = get_team_stats(conn, home_id)  or {}
        as_ = get_team_stats(conn, away_id) or {}

        if not hs and not as_:
            print("  Skipping — no stats for either team")
            continue

        partial = not hs or not as_
        if partial:
            print(f"  Partial data ({'home' if not hs else 'away'} missing — using neutral defaults)")

        # Winner
        winner_pick, winner_conf = predict_winner(hs, as_)
        label = home if winner_pick == "home" else (away if winner_pick == "away" else "Draw")
        upsert_prediction(conn, match_id, "winner", label, winner_conf)
        print(f"  Winner: {label} ({winner_conf}%) [{tier(winner_conf)}]")

        # Corners
        corners_pick, corners_conf = predict_corners(hs, as_)
        upsert_prediction(conn, match_id, "corners", corners_pick, corners_conf)
        print(f"  Corners: {corners_pick} ({corners_conf}%) [{tier(corners_conf)}]")

        # Cards
        cards_pick, cards_conf = predict_cards(hs, as_)
        upsert_prediction(conn, match_id, "cards", cards_pick, cards_conf)
        print(f"  Cards: {cards_pick} ({cards_conf}%) [{tier(cards_conf)}]")

        # Top scorer (home)
        home_scorer, home_scorer_conf = predict_top_scorer(conn, home_id)
        upsert_prediction(conn, match_id, "scorer", home_scorer, home_scorer_conf)
        print(f"  Scorer: {home_scorer} ({home_scorer_conf}%) [{tier(home_scorer_conf)}]")

        # Top scorer (away)
        away_scorer, away_scorer_conf = predict_top_scorer(conn, away_id)
        if away_scorer_conf > home_scorer_conf:
            upsert_prediction(conn, match_id, "scorer", away_scorer, away_scorer_conf)
            print(f"  Scorer (away): {away_scorer} ({away_scorer_conf}%) [{tier(away_scorer_conf)}]")

    conn.commit()
    conn.close()
    print("\nPredictions written OK")


if __name__ == "__main__":
    main()
