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


# ── Model info API ────────────────────────────────────────────────────────────

@app.route("/api/model_info")
def model_info():
    """WC2026 Poisson + shrinkage model metadata (written by predict_wc2026.py)."""
    conn = get_db()
    row = conn.execute("SELECT json FROM model_meta WHERE key='wc_model'").fetchone()
    conn.close()
    if not row:
        return jsonify({"available": False})
    meta = json.loads(row["json"])
    return jsonify({
        "available":    True,
        "model":        meta.get("model", "WC2026 Poisson + shrinkage"),
        "n_wc_matches": meta.get("n_wc_matches", 0),
        "mu_league":    meta.get("mu_league"),
        "k":            meta.get("k"),
        "teams_rated":  meta.get("teams_rated", 0),
        "updated":      meta.get("updated"),
    })


# ── Predictions API ───────────────────────────────────────────────────────────

@app.route("/api/upcoming")
def upcoming():
    """All upcoming matches with WC2026 predictions + tournament stats."""
    conn = get_db()

    cutoff = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat() + "T23:59:59"
    matches = conn.execute("""
        SELECT match_id, home_team, away_team, home_team_id, away_team_id,
               kickoff_utc, stage, group_stage
        FROM matches
        WHERE status IN ('SCHEDULED','TIMED')
          AND home_team IS NOT NULL AND away_team IS NOT NULL
          AND kickoff_utc <= ?
        ORDER BY kickoff_utc
        LIMIT 40
    """, (cutoff,)).fetchall()

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

        # ── WC2026 tournament panels (group table, scorers, fixture path) ──────
        QUALIFY_TARGET = 4  # rough points threshold for the qualification zone

        def wc_stats(team_id, team_name):
            st = conn.execute("""
                SELECT group_label, position, played, won, draw, lost, gf, ga, gd, points
                FROM standings WHERE team_id=? LIMIT 1
            """, (team_id,)).fetchone()

            # Top scorer + team assists from WC scorers (player_stats)
            scorer = conn.execute("""
                SELECT player_name, goals_total FROM player_stats
                WHERE team_id=? AND goals_total > 0
                ORDER BY goals_total DESC LIMIT 1
            """, (team_id,)).fetchone()
            assist_row = conn.execute(
                "SELECT COALESCE(SUM(assists),0) AS a FROM player_stats WHERE team_id=?",
                (team_id,)
            ).fetchone()

            # Past results (finished WC matches for this team)
            past = conn.execute("""
                SELECT home_team, away_team, home_score, away_score
                FROM matches
                WHERE status='FINISHED' AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND (home_team=? OR away_team=?)
                ORDER BY kickoff_utc
            """, (team_name, team_name)).fetchall()
            past_results = []
            for p in past:
                if p["home_team"] == team_name:
                    opp, gf, ga = p["away_team"], p["home_score"], p["away_score"]
                else:
                    opp, gf, ga = p["home_team"], p["away_score"], p["home_score"]
                res = "W" if gf > ga else "L" if gf < ga else "D"
                past_results.append({"opp": opp, "score": f"{gf}-{ga}", "res": res})

            # Remaining group fixtures. (Literal '%' in a LIKE pattern collides with
            # psycopg2's %s parameter substitution on Postgres — stick to stage=.)
            rem = conn.execute("""
                SELECT home_team, away_team FROM matches
                WHERE status IN ('SCHEDULED','TIMED')
                  AND (home_team=? OR away_team=?)
                  AND stage='GROUP_STAGE'
                ORDER BY kickoff_utc
            """, (team_name, team_name)).fetchall()
            remaining = []
            for r in rem:
                opp = r["away_team"] if r["home_team"] == team_name else r["home_team"]
                if opp:
                    remaining.append(opp)

            if not st and not scorer and not past_results and not remaining:
                return None

            points = st["points"] if st else None
            played = st["played"] if st else len(past_results)
            group_left = max(0, 3 - played) if played is not None else None
            qualified = None
            pts_to_qualify = None
            if points is not None:
                if points >= 6:
                    qualified = "Clasificado (prob.)"
                elif group_left and group_left > 0:
                    # Only meaningful while the team still has group games to play.
                    pts_to_qualify = max(0, QUALIFY_TARGET - points)

            return {
                "group":          st["group_label"] if st else None,
                "position":       st["position"] if st else None,
                "points":         points,
                "played":         played,
                "remaining":      group_left,
                "gd":             st["gd"] if st else None,
                "gf":             st["gf"] if st else None,
                "pts_to_qualify": pts_to_qualify,
                "qualified":      qualified,
                "top_scorer":     ({"name": scorer["player_name"], "goals": scorer["goals_total"]}
                                   if scorer else None),
                "team_assists":   assist_row["a"] if assist_row else 0,
                "past_results":   past_results,
                # Only group games still to play; once the group is done, no "falta".
                "remaining_fixtures": remaining if (group_left and group_left > 0) else [],
            }

        home_wc = wc_stats(m["home_team_id"], m["home_team"])
        away_wc = wc_stats(m["away_team_id"], m["away_team"])

        # Team card stats. "Forma 5" is built ONLY from WC2026 results (wc["past_results"],
        # last 5) — never from historical/club data. The rest (fifa_rank, win_pct, etc.)
        # is pre-tournament reference context from martj42, shown separately from form.
        def form(team_name, wc):
            ext_row = conn.execute("""
                SELECT fifa_rank, elo_approx, wc_titles, confederation,
                       win_pct, draw_pct, loss_pct,
                       comp_gf_avg, comp_ga_avg, comp_win_pct,
                       clean_sheet_pct, big_win_pct, big_loss_pct,
                       pts_pct_5, pts_pct_10
                FROM team_extended_stats WHERE team=? LIMIT 1
            """, (team_name,)).fetchone()

            form_list = [r["res"] for r in (wc["past_results"] if wc else [])][-5:]

            if not ext_row and not form_list:
                return None

            return {
                "form":            form_list,
                "fifa_rank":       ext_row["fifa_rank"]       if ext_row else None,
                "elo":             ext_row["elo_approx"]      if ext_row else None,
                "wc_titles":       ext_row["wc_titles"]       if ext_row else 0,
                "confederation":   ext_row["confederation"]   if ext_row else "",
                "win_pct":         ext_row["win_pct"]         if ext_row else None,
                "draw_pct":        ext_row["draw_pct"]        if ext_row else None,
                "loss_pct":        ext_row["loss_pct"]        if ext_row else None,
                "comp_gf_avg":     ext_row["comp_gf_avg"]     if ext_row else None,
                "comp_ga_avg":     ext_row["comp_ga_avg"]     if ext_row else None,
                "comp_win_pct":    ext_row["comp_win_pct"]    if ext_row else None,
                "clean_sheet_pct": ext_row["clean_sheet_pct"] if ext_row else None,
                "big_win_pct":     ext_row["big_win_pct"]     if ext_row else None,
                "big_loss_pct":    ext_row["big_loss_pct"]    if ext_row else None,
                "pts_pct_5":       ext_row["pts_pct_5"]       if ext_row else None,
                "pts_pct_10":      ext_row["pts_pct_10"]      if ext_row else None,
            }

        home_stats = form(m["home_team"], home_wc)
        away_stats = form(m["away_team"], away_wc)

        # Build predictions dict (corners/cards already dropped by predict_wc2026.py)
        preds_out = []
        for p in preds:
            preds_out.append({
                "market":     p["market"],
                "pick":       p["pick"],
                "confidence": p["confidence"],
                "tier":       p["stake_tier"],
                "odds":       p["odds"],
            })

        # Reddit sentiment (optional — populated by fetch_reddit_sentiment.py)
        sentiment = None
        try:
            s = conn.execute(
                "SELECT home_win_pct, draw_pct, away_win_pct, summary, top_themes, post_count "
                "FROM match_sentiment WHERE match_id=?", (mid,)
            ).fetchone()
            if s:
                sentiment = {
                    "home_win_pct": s["home_win_pct"],
                    "draw_pct":     s["draw_pct"],
                    "away_win_pct": s["away_win_pct"],
                    "summary":      s["summary"],
                    "top_themes":   json.loads(s["top_themes"] or "[]"),
                    "post_count":   s["post_count"],
                }
        except Exception:
            pass

        result.append({
            "match_id":    mid,
            "home":        m["home_team"],
            "away":        m["away_team"],
            "kickoff_pe":  pe_time(m["kickoff_utc"]),
            "kickoff_utc": m["kickoff_utc"],
            "stage":       (m["stage"] or "").replace("_", " ").title(),
            "home_stats":  home_stats,
            "away_stats":  away_stats,
            "home_wc":     home_wc,
            "away_wc":     away_wc,
            "predictions": preds_out,
            "sentiment":   sentiment,
        })

    conn.close()
    return jsonify(result)


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 8000))
    debug = os.getenv("ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
