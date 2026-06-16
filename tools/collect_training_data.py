"""
Collect and prepare training data for Dixon-Coles model.
Source: martj42/international_results CSV (free, no API).
Filters: 2022-01-01 to today, competitive only (no friendlies).
Weights: time decay (half-life=1yr) × competition quality.
Output: SQLite table training_matches + team_params_init.
"""
import sqlite3, json
from pathlib import Path
from datetime import date

import pandas as pd
import numpy as np

BASE  = Path(__file__).parent.parent
DB    = BASE / "database" / "sports_agent.db"
CACHE = BASE / ".tmp" / "intl_results.csv"

# Competition keyword → quality weight
COMP_WEIGHTS = [
    ("FIFA World Cup qualification", 0.85),
    ("FIFA World Cup",               1.00),
    ("UEFA Euro qualification",      0.75),
    ("UEFA Euro",                    0.90),
    ("UEFA Nations League",          0.80),
    ("Copa América",                 0.90),
    ("Copa America",                 0.90),
    ("Africa Cup of Nations",        0.75),
    ("AFCON",                        0.75),
    ("AFC Asian Cup",                0.70),
    ("CONCACAF Gold Cup",            0.70),
    ("CONCACAF Nations League",      0.65),
    ("Friendly",                     0.00),   # excluded
    ("friendly",                     0.00),
]

# Time decay: half-life 365 days
DECAY_HALFLIFE = 365.0
PHI = np.log(2) / DECAY_HALFLIFE

# WC 2026 host countries (get small home advantage boost)
WC_HOSTS = {"United States", "Canada", "Mexico"}


def comp_weight(tournament: str) -> float:
    t = str(tournament)
    for keyword, w in COMP_WEIGHTS:
        if keyword.lower() in t.lower():
            return w
    return 0.60  # default competitive


def get_wc_teams() -> set:
    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT DISTINCT home_team FROM matches WHERE home_team IS NOT NULL
        UNION
        SELECT DISTINCT away_team FROM matches WHERE away_team IS NOT NULL
    """).fetchall()
    conn.close()
    return {r[0] for r in rows}


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS training_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            home_team TEXT, away_team TEXT,
            home_goals INTEGER, away_goals INTEGER,
            tournament TEXT,
            neutral INTEGER,
            comp_weight REAL,
            time_weight REAL,
            total_weight REAL,
            home_is_wc INTEGER, away_is_wc INTEGER
        );

        CREATE TABLE IF NOT EXISTS dc_team_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT UNIQUE
        );
    """)
    conn.commit()


def inject_wc2026_results(conn, today) -> list:
    """Inject finished WC 2026 matches from local DB with max weight (2x boost)."""
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)").fetchall()}
    if "home_score" not in existing_cols:
        return []

    rows = conn.execute("""
        SELECT home_team, away_team, kickoff_utc, home_score, away_score
        FROM matches
        WHERE status='FINISHED'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND home_team IS NOT NULL AND away_team IS NOT NULL
    """).fetchall()

    records = []
    for r in rows:
        match_date = r[2][:10]
        days_ago = max(0, (today - pd.Timestamp(match_date)).days)
        time_w = np.exp(-PHI * days_ago)
        total_w = round(1.0 * time_w * 2.0, 4)  # comp_weight=1.0, 2x WC2026 boost
        records.append((
            match_date,
            r[0], r[1],
            int(r[3]), int(r[4]),
            "FIFA World Cup 2026",
            0, 1.0, round(time_w, 4), total_w, 1, 1,
        ))
    if records:
        print(f"  Injecting {len(records)} WC 2026 finished matches (2x weight)")
    return records


def main():
    if not CACHE.exists():
        raise FileNotFoundError(
            "intl_results.csv not found. Run: python tools/fetch_intl_stats.py first"
        )

    print("Loading martj42 CSV…")
    df = pd.read_csv(CACHE, parse_dates=["date"])
    df = df.dropna(subset=["home_score", "away_score"])
    df["home_score"] = df["home_score"].astype(int)
    df["away_score"] = df["away_score"].astype(int)

    # Date filter
    cutoff_start = pd.Timestamp("2022-01-01")
    df = df[df["date"] >= cutoff_start].copy()
    print(f"After date filter (>= 2022): {len(df):,} matches")

    # Competition weight + exclude friendlies
    df["comp_w"] = df["tournament"].apply(comp_weight)
    df = df[df["comp_w"] > 0].copy()
    print(f"After excluding friendlies: {len(df):,} matches")

    # Time decay weight
    today = pd.Timestamp(date.today())
    df["days_ago"] = (today - df["date"]).dt.days
    df["time_w"]   = np.exp(-PHI * df["days_ago"])

    # Total weight
    df["total_w"] = df["comp_w"] * df["time_w"]

    # Tag WC 2026 teams
    wc_teams = get_wc_teams()
    df["home_is_wc"] = df["home_team"].isin(wc_teams).astype(int)
    df["away_is_wc"] = df["away_team"].isin(wc_teams).astype(int)

    # Keep only matches where at least one team is in WC 2026
    df = df[(df["home_is_wc"] == 1) | (df["away_is_wc"] == 1)].copy()
    print(f"After WC team filter: {len(df):,} matches")

    # All unique teams in training set (for parameter indexing)
    all_teams = sorted(
        set(df["home_team"].unique()) | set(df["away_team"].unique())
    )
    print(f"Unique teams in training data: {len(all_teams)}")

    # Write to DB
    conn = sqlite3.connect(DB)
    init_db(conn)
    conn.execute("DELETE FROM training_matches")
    conn.execute("DELETE FROM dc_team_index")

    for i, team in enumerate(all_teams):
        conn.execute(
            "INSERT INTO dc_team_index (team) VALUES (?)", (team,)
        )

    records = []
    for _, row in df.iterrows():
        records.append((
            row["date"].strftime("%Y-%m-%d"),
            row["home_team"], row["away_team"],
            int(row["home_score"]), int(row["away_score"]),
            row["tournament"],
            int(row.get("neutral", 0) or 0),
            round(row["comp_w"], 4),
            round(row["time_w"], 4),
            round(row["total_w"], 4),
            int(row["home_is_wc"]),
            int(row["away_is_wc"]),
        ))

    wc2026 = inject_wc2026_results(conn, today)
    records.extend(wc2026)

    conn.executemany("""
        INSERT INTO training_matches
            (date, home_team, away_team, home_goals, away_goals, tournament,
             neutral, comp_weight, time_weight, total_weight, home_is_wc, away_is_wc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, records)
    conn.commit()

    # Summary stats
    wc_only = df[(df["home_is_wc"] == 1) & (df["away_is_wc"] == 1)]
    avg_goals = (df["home_score"].sum() + df["away_score"].sum()) / (2 * len(df))

    print(f"\nTraining set summary:")
    print(f"  Total matches:              {len(df):>5}")
    print(f"  WC vs WC matches:           {len(wc_only):>5}")
    print(f"  Avg goals per team/game:    {avg_goals:.3f}")
    print(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    print(f"  Matches in DB: training_matches table")
    print(f"\nTop competition breakdown:")
    print(df.groupby("tournament")["comp_w"].count().sort_values(ascending=False).head(10).to_string())

    conn.close()
    print("\nDone. Next: python tools/train_model.py")


if __name__ == "__main__":
    main()
