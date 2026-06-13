"""Log bets placed and settle results. CLI tool."""
import argparse, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            home_team TEXT,
            away_team TEXT,
            market TEXT,
            pick TEXT,
            odds REAL,
            stake_soles REAL,
            result TEXT DEFAULT 'pending',
            profit_soles REAL DEFAULT 0,
            placed_at TEXT,
            settled_at TEXT
        )
    """)
    conn.commit()


def place_bet(conn, args):
    # Resolve team names from match_id if available
    row = conn.execute(
        "SELECT home_team, away_team FROM matches WHERE match_id=?", (args.match_id,)
    ).fetchone()
    home, away = (row[0], row[1]) if row else ("", "")

    conn.execute("""
        INSERT INTO bets (match_id, home_team, away_team, market, pick, odds, stake_soles, placed_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (args.match_id, home, away, args.market, args.pick,
          args.odds, args.stake, datetime.now(timezone.utc).isoformat()))
    conn.commit()

    bet_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"Bet logged. ID={bet_id} | {home} vs {away} | {args.market} | {args.pick} @ {args.odds} | S/.{args.stake}")


def settle_bet(conn, args):
    row = conn.execute("SELECT odds, stake_soles FROM bets WHERE id=?", (args.bet_id,)).fetchone()
    if not row:
        print(f"Bet ID {args.bet_id} not found")
        return

    odds, stake = row
    if args.result == "win":
        profit = round((odds - 1) * stake, 2)
    elif args.result == "loss":
        profit = -stake
    else:  # void
        profit = 0.0

    conn.execute("""
        UPDATE bets SET result=?, profit_soles=?, settled_at=? WHERE id=?
    """, (args.result, profit, datetime.now(timezone.utc).isoformat(), args.bet_id))
    conn.commit()

    sign = "+" if profit >= 0 else ""
    print(f"Bet {args.bet_id} settled: {args.result} | {sign}S/.{profit}")


def list_bets(conn):
    rows = conn.execute("""
        SELECT id, home_team, away_team, market, pick, odds, stake_soles, result, profit_soles, placed_at
        FROM bets ORDER BY placed_at DESC LIMIT 20
    """).fetchall()
    if not rows:
        print("No bets logged yet")
        return
    print(f"{'ID':<4} {'Match':<30} {'Market':<10} {'Pick':<20} {'Odds':<6} {'Stake':>7} {'Result':<8} {'P&L':>8}")
    print("-" * 100)
    for r in rows:
        match = f"{r[1]} vs {r[2]}"[:29]
        sign = "+" if r[8] >= 0 else ""
        print(f"{r[0]:<4} {match:<30} {r[3]:<10} {r[4]:<20} {r[5]:<6.2f} S/.{r[6]:>5.0f} {r[7]:<8} {sign}S/.{r[8]:>5.2f}")


def main():
    parser = argparse.ArgumentParser(description="Bet tracker CLI")
    sub = parser.add_subparsers(dest="action", required=True)

    p_place = sub.add_parser("place", help="Log a new bet")
    p_place.add_argument("--match_id", required=True)
    p_place.add_argument("--market", required=True, choices=["winner","scorer","corners","cards","1h_scorer","2h_scorer"])
    p_place.add_argument("--pick", required=True)
    p_place.add_argument("--odds", type=float, required=True)
    p_place.add_argument("--stake", type=float, required=True)

    p_settle = sub.add_parser("settle", help="Settle a bet result")
    p_settle.add_argument("--bet_id", type=int, required=True)
    p_settle.add_argument("--result", required=True, choices=["win","loss","void"])

    sub.add_parser("list", help="Show recent bets")

    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if args.action == "place":
        place_bet(conn, args)
    elif args.action == "settle":
        settle_bet(conn, args)
    elif args.action == "list":
        list_bets(conn)

    conn.close()


if __name__ == "__main__":
    main()
