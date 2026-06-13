"""Flask P&L + Predictions dashboard."""
import os, json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify
from dotenv import load_dotenv
from db import get_db, init_db

load_dotenv()

app = Flask(__name__)


def pe_time(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        pe = dt.astimezone(timezone(timedelta(hours=-5)))
        return pe.strftime("%b %d · %H:%M")
    except Exception:
        return utc_str[:16]


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── P&L API ───────────────────────────────────────────────────────────────────

@app.route("/api/summary")
def summary():
    bankroll_start = float(os.getenv("BANKROLL_START", 325))
    conn = get_db()
    t = conn.execute("""
        SELECT COUNT(*) as total_bets,
               SUM(CASE WHEN result='win'     THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss'    THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result='pending' THEN 1 ELSE 0 END) as pending,
               SUM(profit_soles)  as total_profit,
               SUM(stake_soles)   as total_staked
        FROM bets WHERE result != 'pending'
    """).fetchone()
    conn.close()

    settled      = (t["wins"] or 0) + (t["losses"] or 0)
    win_rate     = round(t["wins"] / settled * 100, 1) if settled > 0 else 0
    total_profit = t["total_profit"] or 0
    total_staked = t["total_staked"] or 0
    roi          = round(total_profit / total_staked * 100, 1) if total_staked > 0 else 0

    return jsonify({
        "bankroll_start": bankroll_start,
        "bankroll_now":   round(bankroll_start + total_profit, 2),
        "total_profit":   round(total_profit, 2),
        "roi_pct":        roi,
        "win_rate":       win_rate,
        "total_bets":     t["total_bets"] or 0,
        "wins":           t["wins"] or 0,
        "losses":         t["losses"] or 0,
        "pending":        t["pending"] or 0,
    })


@app.route("/api/by_market")
def by_market():
    conn = get_db()
    rows = conn.execute("""
        SELECT market,
               COUNT(*) as bets,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
               SUM(profit_soles) as profit,
               SUM(stake_soles)  as staked
        FROM bets WHERE result != 'pending'
        GROUP BY market ORDER BY profit DESC
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        staked = r["staked"] or 0
        roi    = round(r["profit"] / staked * 100, 1) if staked > 0 else 0
        result.append({
            "market":   r["market"],
            "bets":     r["bets"],
            "wins":     r["wins"] or 0,
            "win_rate": round((r["wins"] or 0) / r["bets"] * 100, 1),
            "profit":   round(r["profit"] or 0, 2),
            "roi":      roi,
        })
    return jsonify(result)


@app.route("/api/bankroll_history")
def bankroll_history():
    bankroll_start = float(os.getenv("BANKROLL_START", 325))
    conn = get_db()
    rows = conn.execute("""
        SELECT settled_at, profit_soles FROM bets
        WHERE result != 'pending' AND settled_at IS NOT NULL
        ORDER BY settled_at
    """).fetchall()
    conn.close()

    history = [{"date": "Start", "bankroll": bankroll_start}]
    running = bankroll_start
    for r in rows:
        running = round(running + (r["profit_soles"] or 0), 2)
        history.append({"date": r["settled_at"][:10], "bankroll": running})
    return jsonify(history)


@app.route("/api/recent_bets")
def recent_bets():
    conn = get_db()
    rows = conn.execute("""
        SELECT b.id, m.home_team, m.away_team, b.market, b.pick, b.odds,
               b.stake_soles, b.result, b.profit_soles, b.placed_at
        FROM bets b
        LEFT JOIN matches m ON b.match_id = m.match_id
        ORDER BY b.placed_at DESC LIMIT 30
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Model info API ────────────────────────────────────────────────────────────

@app.route("/api/model_info")
def model_info():
    conn = get_db()
    dc_row   = conn.execute("SELECT json FROM model_meta WHERE key='dc_params'").fetchone()
    eval_row = conn.execute("SELECT json FROM model_meta WHERE key='eval_report'").fetchone()
    conn.close()

    if dc_row:
        dc = json.loads(dc_row["json"])
        eval_report = json.loads(eval_row["json"]) if eval_row else {}
    else:
        # Local dev fallback: read straight from models/ before first sync
        models_dir = Path(__file__).parent.parent / "models"
        dc_file   = models_dir / "dc_params.json"
        eval_file = models_dir / "eval_report.json"
        if not dc_file.exists():
            return jsonify({"available": False})
        dc   = json.loads(dc_file.read_text(encoding="utf-8"))
        eval_report = json.loads(eval_file.read_text(encoding="utf-8")) if eval_file.exists() else {}

    return jsonify({
        "available":   True,
        "n_teams":     len(dc.get("teams", [])),
        "n_train":     dc.get("n_train"),
        "gamma":       round(dc.get("gamma", 0), 3),
        "rho":         round(dc.get("rho", 0), 3),
        "rps":         round(eval_report.get("dc_rps", 0), 4),
        "naive_rps":   round(eval_report.get("naive_rps", 0), 4),
        "skill_pct":   round(eval_report.get("skill_pct", 0), 1),
        "holdout_n":   eval_report.get("holdout_n"),
    })


# ── Predictions API ───────────────────────────────────────────────────────────

@app.route("/api/upcoming")
def upcoming():
    """All upcoming matches with predictions + team form."""
    stake = {
        "HIGH": float(os.getenv("STAKE_HIGH", 50)),
        "MED":  float(os.getenv("STAKE_MED",  25)),
        "LOW":  float(os.getenv("STAKE_LOW",  10)),
    }
    conn = get_db()

    matches = conn.execute("""
        SELECT match_id, home_team, away_team, home_team_id, away_team_id,
               kickoff_utc, stage, group_stage
        FROM matches
        WHERE status IN ('SCHEDULED','TIMED')
          AND home_team IS NOT NULL AND away_team IS NOT NULL
          AND date(kickoff_utc) <= date('now', '+30 days')
        ORDER BY kickoff_utc
        LIMIT 40
    """).fetchall()

    result = []
    for m in matches:
        mid = m["match_id"]

        # Predictions
        preds = conn.execute("""
            SELECT market, pick, confidence, stake_tier, odds
            FROM predictions
            WHERE match_id=?
            ORDER BY confidence DESC
        """, (mid,)).fetchall()

        # include even matches without predictions (shown as "pending data")

        LEAGUE_LABEL = {
            5: "UEFA EURO", 6: "UEFA NL", 9: "Copa América",
            22: "CONCACAF GC", 536: "CONCACAF NL", 13: "AFCON",
            17: "AFC Cup", 10: "Amistosos", 0: "",
        }
        LEAGUE_STARS = {
            5: "★★★★★", 6: "★★★★★", 9: "★★★★",
            22: "★★★", 536: "★★★", 13: "★★★",
            17: "★★", 10: "★★", 0: "",
        }

        # Team form — joins api-sports.io stats + martj42 extended stats
        def form(team_id, team_name):
            api_row = conn.execute("""
                SELECT form_last10, goals_scored_avg, goals_conceded_avg,
                       avg_cards, avg_corners_for, avg_corners_against, league_id
                FROM team_stats WHERE team_id=? ORDER BY stat_date DESC LIMIT 1
            """, (team_id,)).fetchone()

            ext_row = conn.execute("""
                SELECT fifa_rank, elo_approx, wc_titles, confederation,
                       win_pct, draw_pct, loss_pct,
                       comp_gf_avg, comp_ga_avg, comp_win_pct,
                       clean_sheet_pct, big_win_pct, big_loss_pct,
                       form5, form10, pts_pct_5, pts_pct_10
                FROM team_extended_stats WHERE team=? LIMIT 1
            """, (team_name,)).fetchone()

            if not api_row and not ext_row:
                return None

            # Form from martj42 (more teams, more reliable)
            form_list = []
            if ext_row and ext_row["form10"]:
                form_list = list(ext_row["form10"])
            elif api_row:
                try:
                    form_list = json.loads(api_row["form_last10"] or "[]")
                except Exception:
                    form_list = []

            lid = (api_row["league_id"] if api_row else 0) or 0

            return {
                "form":              form_list,
                "gf":                round((api_row["goals_scored_avg"]  if api_row else 0) or 0, 2),
                "ga":                round((api_row["goals_conceded_avg"] if api_row else 0) or 0, 2),
                "cards":             round((api_row["avg_cards"]          if api_row else 0) or 0, 1),
                "corners_for":       round((api_row["avg_corners_for"]    if api_row else 0) or 0, 1),
                "corners_against":   round((api_row["avg_corners_against"] if api_row else 0) or 0, 1),
                "league":            LEAGUE_LABEL.get(lid, ""),
                "league_stars":      LEAGUE_STARS.get(lid, ""),
                # Extended stats from martj42
                "fifa_rank":         ext_row["fifa_rank"]       if ext_row else None,
                "elo":               ext_row["elo_approx"]      if ext_row else None,
                "wc_titles":         ext_row["wc_titles"]       if ext_row else 0,
                "confederation":     ext_row["confederation"]   if ext_row else "",
                "win_pct":           ext_row["win_pct"]         if ext_row else None,
                "draw_pct":          ext_row["draw_pct"]        if ext_row else None,
                "loss_pct":          ext_row["loss_pct"]        if ext_row else None,
                "comp_gf_avg":       ext_row["comp_gf_avg"]     if ext_row else None,
                "comp_ga_avg":       ext_row["comp_ga_avg"]     if ext_row else None,
                "comp_win_pct":      ext_row["comp_win_pct"]    if ext_row else None,
                "clean_sheet_pct":   ext_row["clean_sheet_pct"] if ext_row else None,
                "big_win_pct":       ext_row["big_win_pct"]     if ext_row else None,
                "big_loss_pct":      ext_row["big_loss_pct"]    if ext_row else None,
                "pts_pct_5":         ext_row["pts_pct_5"]       if ext_row else None,
                "pts_pct_10":        ext_row["pts_pct_10"]      if ext_row else None,
            }

        home_stats = form(m["home_team_id"], m["home_team"])
        away_stats = form(m["away_team_id"], m["away_team"])

        # Build predictions dict
        preds_out = []
        bets_out  = []
        for p in preds:
            preds_out.append({
                "market":     p["market"],
                "pick":       p["pick"],
                "confidence": p["confidence"],
                "tier":       p["stake_tier"],
                "odds":       p["odds"],
            })
            if p["stake_tier"] in ("HIGH", "MED"):
                bets_out.append({
                    "pick":  p["pick"],
                    "tier":  p["stake_tier"],
                    "stake": stake[p["stake_tier"]],
                    "odds":  p["odds"],
                })

        result.append({
            "match_id":    mid,
            "home":        m["home_team"],
            "away":        m["away_team"],
            "kickoff_pe":  pe_time(m["kickoff_utc"]),
            "kickoff_utc": m["kickoff_utc"],
            "stage":       (m["stage"] or "").replace("_", " ").title(),
            "home_stats":  home_stats,
            "away_stats":  away_stats,
            "predictions": preds_out,
            "bets":        bets_out,
        })

    conn.close()
    return jsonify(result)


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 8000))
    debug = os.getenv("ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
