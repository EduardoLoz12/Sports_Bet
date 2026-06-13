"""Build Telegram message text for each upcoming match.
Usage: python generate_report.py          # D-1 only (production)
       python generate_report.py --preview # all upcoming with predictions
"""
import sqlite3, os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"

STAKE = {
    "HIGH": float(os.getenv("STAKE_HIGH", 50)),
    "MED":  float(os.getenv("STAKE_MED", 25)),
    "LOW":  float(os.getenv("STAKE_LOW", 10)),
}

MARKET_EMOJI = {
    "winner":  "📊",
    "scorer":  "⚽",
    "corners": "📐",
    "cards":   "🟨",
    "1h_scorer": "⏱",
    "2h_scorer": "⏱",
}

MIN_CONFIDENCE = 40


def get_upcoming_matches(conn: sqlite3.Connection, preview: bool = False) -> list:
    if preview:
        date_filter = "date(kickoff_utc) <= date('now', '+30 days')"
    else:
        date_filter = "date(kickoff_utc) = date('now', '+1 day')"
    return conn.execute(f"""
        SELECT match_id, home_team, away_team, kickoff_utc, stage, group_stage
        FROM matches
        WHERE status IN ('SCHEDULED','TIMED')
          AND home_team IS NOT NULL AND away_team IS NOT NULL
          AND {date_filter}
        ORDER BY kickoff_utc
    """).fetchall()


def get_predictions(conn: sqlite3.Connection, match_id: str) -> list:
    return conn.execute("""
        SELECT market, pick, confidence, stake_tier, odds
        FROM predictions WHERE match_id=? AND confidence >= ?
        ORDER BY confidence DESC
    """, (match_id, MIN_CONFIDENCE)).fetchall()


def format_kickoff(utc_str: str) -> str:
    from datetime import datetime, timezone, timedelta
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        pe = dt.astimezone(timezone(timedelta(hours=-5)))
        return pe.strftime("%b %d · %H:%M PE")
    except Exception:
        return utc_str[:16]


def build_message(match: tuple, predictions: list) -> str:
    match_id, home, away, kickoff, stage, group = match

    stage_label = (stage or "").replace("_", " ").title()

    lines = [
        f"🏆 *{home}* vs *{away}*",
        f"📅 {format_kickoff(kickoff)} | {stage_label}",
        "",
    ]

    bet_lines = []
    for market, pick, conf, stake_tier, odds in predictions:
        emoji = MARKET_EMOJI.get(market, "•")
        odds_str = f"@ {odds:.2f}" if odds > 1.0 else ""
        lines.append(f"{emoji} {market.upper()}: {pick} — {conf}% [{stake_tier}]")
        if stake_tier in ("HIGH", "MED") and market not in ("probabilities", "scoreline"):
            stake = STAKE[stake_tier]
            bet_lines.append(f"  • {market.upper()}: {pick} {odds_str} → S/.{stake:.0f} [{stake_tier}]")

    if bet_lines:
        lines.append("")
        lines.append("💰 *Recommended bets:*")
        lines.extend(bet_lines)
    else:
        lines.append("")
        lines.append("💰 No HIGH/MED confidence bets today.")

    return "\n".join(lines)


def main(preview: bool = False) -> list[str]:
    if not preview:
        preview = "--preview" in sys.argv
    conn = sqlite3.connect(DB_PATH)
    matches = get_upcoming_matches(conn, preview=preview)

    if not matches:
        label = "in next 30 days" if preview else "tomorrow"
        print(f"No matches {label} — nothing to report")
        conn.close()
        return []

    messages = []
    for match in matches:
        predictions = get_predictions(conn, match[0])
        msg = build_message(match, predictions)
        messages.append(msg)
        print(f"--- Report: {match[1]} vs {match[2]} ---")
        print(msg)
        print()

    conn.close()
    return messages


if __name__ == "__main__":
    main()
