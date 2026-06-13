"""
Compute team stats from martj42/international_results CSV + static meta.
Writes to team_extended_stats table in SQLite.
No API calls — completely free, no rate limits.
Run: python tools/fetch_intl_stats.py
"""
import json, sqlite3, unicodedata
from pathlib import Path
from datetime import date

import pandas as pd
import requests, urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE   = Path(__file__).parent.parent
DB     = BASE / "database" / "sports_agent.db"
META   = BASE / "data" / "static" / "team_meta.json"
CACHE  = BASE / ".tmp" / "intl_results.csv"

CSV_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"

FD_TO_M42 = {
    "United States":          "United States",
    "South Korea":            "South Korea",
    "Congo DR":               "DR Congo",
    "Ivory Coast":            "Ivory Coast",
    "Bosnia-Herzegovina":     "Bosnia and Herzegovina",
    "Czechia":                "Czech Republic",
}


def norm(s):
    return unicodedata.normalize("NFC", str(s)).casefold()


def fetch_csv() -> pd.DataFrame:
    today = date.today().isoformat()
    stamp = CACHE.parent / "intl_results_date.txt"
    if CACHE.exists() and stamp.exists() and stamp.read_text().strip() == today:
        print("Using cached intl_results.csv")
    else:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print("Downloading martj42 CSV…")
        r = requests.get(CSV_URL, timeout=30, verify=False)
        r.raise_for_status()
        CACHE.write_bytes(r.content)
        stamp.write_text(today)
        print(f"  {len(r.content)//1024} KB downloaded")
    df = pd.read_csv(CACHE, parse_dates=["date"])
    # Drop unplayed matches (null scores)
    df = df.dropna(subset=["home_score", "away_score"])
    return df


def get_wc_teams() -> list[str]:
    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT DISTINCT home_team FROM matches WHERE home_team IS NOT NULL
        UNION
        SELECT DISTINCT away_team FROM matches WHERE away_team IS NOT NULL
    """).fetchall()
    conn.close()
    return sorted(r[0] for r in rows)


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_extended_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT UNIQUE,
            fifa_rank INTEGER,
            elo_approx INTEGER,
            wc_titles INTEGER,
            continental_titles INTEGER,
            confederation TEXT,
            total_matches INTEGER,
            win_pct REAL, draw_pct REAL, loss_pct REAL,
            gf_avg REAL, ga_avg REAL, goal_diff_avg REAL,
            comp_gf_avg REAL, comp_ga_avg REAL, comp_win_pct REAL,
            clean_sheet_pct REAL, big_win_pct REAL, big_loss_pct REAL,
            form5 TEXT, form10 TEXT,
            pts_pct_5 REAL, pts_pct_10 REAL, pts_pct_20 REAL,
            last_match TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def calc(df: pd.DataFrame, fd_name: str, all_teams_in_csv: set) -> dict:
    m42 = FD_TO_M42.get(fd_name, fd_name)
    # Normalize fallback
    if m42 not in all_teams_in_csv:
        matches_norm = [t for t in all_teams_in_csv if norm(t) == norm(m42)]
        if matches_norm:
            m42 = matches_norm[0]

    home = df[df["home_team"] == m42].copy()
    home["gf"] = home["home_score"]
    home["ga"] = home["away_score"]

    away = df[df["away_team"] == m42].copy()
    away["gf"] = away["away_score"]
    away["ga"] = away["home_score"]

    cols = ["date", "home_team", "away_team", "gf", "ga", "tournament"]
    matches = pd.concat([home[cols], away[cols]]).sort_values("date", ascending=False)
    cutoff  = pd.Timestamp("2022-01-01")
    recent  = matches[matches["date"] >= cutoff].head(30).copy()

    if recent.empty:
        return {}

    recent["result"] = recent.apply(
        lambda r: "W" if r.gf > r.ga else ("D" if r.gf == r.ga else "L"), axis=1
    )
    recent["cs"]  = recent["ga"] == 0
    recent["bw"]  = (recent["gf"] - recent["ga"]) >= 3
    recent["bl"]  = (recent["ga"] - recent["gf"]) >= 3

    n = len(recent)
    w = (recent["result"] == "W").sum()
    d = (recent["result"] == "D").sum()
    l = (recent["result"] == "L").sum()

    def form_pts(lst):
        return sum(3 if r == "W" else 1 if r == "D" else 0 for r in lst)

    r5  = recent.head(5)["result"].tolist()
    r10 = recent.head(10)["result"].tolist()
    r20 = recent.head(20)["result"].tolist()

    comp = recent[~recent["tournament"].str.contains("Friendly", case=False, na=False)]

    return {
        "total_matches":    n,
        "win_pct":          round(w / n * 100, 1),
        "draw_pct":         round(d / n * 100, 1),
        "loss_pct":         round(l / n * 100, 1),
        "gf_avg":           round(recent["gf"].mean(), 2),
        "ga_avg":           round(recent["ga"].mean(), 2),
        "goal_diff_avg":    round((recent["gf"] - recent["ga"]).mean(), 2),
        "comp_gf_avg":      round(comp["gf"].mean(), 2) if not comp.empty else None,
        "comp_ga_avg":      round(comp["ga"].mean(), 2) if not comp.empty else None,
        "comp_win_pct":     round((comp["result"] == "W").mean() * 100, 1) if not comp.empty else None,
        "clean_sheet_pct":  round(recent["cs"].mean() * 100, 1),
        "big_win_pct":      round(recent["bw"].mean() * 100, 1),
        "big_loss_pct":     round(recent["bl"].mean() * 100, 1),
        "form5":            "".join(r5),
        "form10":           "".join(r10),
        "pts_pct_5":        round(form_pts(r5)  / (len(r5)  * 3) * 100, 1) if r5  else 0,
        "pts_pct_10":       round(form_pts(r10) / (len(r10) * 3) * 100, 1) if r10 else 0,
        "pts_pct_20":       round(form_pts(r20) / (len(r20) * 3) * 100, 1) if r20 else 0,
        "last_match":       recent.iloc[0]["date"].strftime("%Y-%m-%d"),
    }


def main():
    df      = fetch_csv()
    teams   = get_wc_teams()
    meta    = json.loads(META.read_text(encoding="utf-8"))
    all_csv = set(df["home_team"].dropna().unique()) | set(df["away_team"].dropna().unique())

    conn = sqlite3.connect(DB)
    init_db(conn)

    ok = skip = 0
    for fd_name in teams:
        s = calc(df, fd_name, all_csv)
        m = meta.get(fd_name, {})
        if not s:
            print(f"  SKIP {fd_name} — no data in martj42")
            skip += 1
            continue

        conn.execute("""
            INSERT INTO team_extended_stats
                (team, fifa_rank, elo_approx, wc_titles, continental_titles, confederation,
                 total_matches, win_pct, draw_pct, loss_pct,
                 gf_avg, ga_avg, goal_diff_avg,
                 comp_gf_avg, comp_ga_avg, comp_win_pct,
                 clean_sheet_pct, big_win_pct, big_loss_pct,
                 form5, form10, pts_pct_5, pts_pct_10, pts_pct_20,
                 last_match, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(team) DO UPDATE SET
                fifa_rank=excluded.fifa_rank, elo_approx=excluded.elo_approx,
                wc_titles=excluded.wc_titles, continental_titles=excluded.continental_titles,
                confederation=excluded.confederation,
                total_matches=excluded.total_matches,
                win_pct=excluded.win_pct, draw_pct=excluded.draw_pct, loss_pct=excluded.loss_pct,
                gf_avg=excluded.gf_avg, ga_avg=excluded.ga_avg, goal_diff_avg=excluded.goal_diff_avg,
                comp_gf_avg=excluded.comp_gf_avg, comp_ga_avg=excluded.comp_ga_avg,
                comp_win_pct=excluded.comp_win_pct,
                clean_sheet_pct=excluded.clean_sheet_pct,
                big_win_pct=excluded.big_win_pct, big_loss_pct=excluded.big_loss_pct,
                form5=excluded.form5, form10=excluded.form10,
                pts_pct_5=excluded.pts_pct_5, pts_pct_10=excluded.pts_pct_10,
                pts_pct_20=excluded.pts_pct_20,
                last_match=excluded.last_match, updated_at=datetime('now')
        """, (
            fd_name,
            m.get("fifa_rank"), m.get("elo_approx"),
            m.get("wc_titles", 0), m.get("continental_titles", 0), m.get("confederation", ""),
            s["total_matches"], s["win_pct"], s["draw_pct"], s["loss_pct"],
            s["gf_avg"], s["ga_avg"], s["goal_diff_avg"],
            s.get("comp_gf_avg"), s.get("comp_ga_avg"), s.get("comp_win_pct"),
            s["clean_sheet_pct"], s["big_win_pct"], s["big_loss_pct"],
            s["form5"], s["form10"],
            s["pts_pct_5"], s["pts_pct_10"], s["pts_pct_20"],
            s["last_match"],
        ))
        conn.commit()
        print(f"  OK {fd_name:25s} W={s['win_pct']}% CS={s['clean_sheet_pct']}% form5={s['form5']}")
        ok += 1

    conn.close()
    print(f"\nDone. OK={ok} SKIP={skip}")
    print("Next: python tools/scoring_model.py")


if __name__ == "__main__":
    main()
