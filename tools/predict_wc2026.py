"""
WC 2026 predictions — Poisson with shrinkage, trained ONLY on World Cup 2026 results.

No historical international data, no Dixon-Coles MLE. Each team's attack/defense rate
is computed from its finished WC2026 matches and shrunk toward the tournament mean so
that teams with only 1-2 games don't produce wild ratings.

For each upcoming match:
  lam = mu * A_home * D_away   (expected home goals)
  mu_a = mu * A_away * D_home  (expected away goals)
  -> bivariate Poisson grid -> P(home/draw/away) + most likely scoreline.

Markets written: winner, probabilities, scoreline. (No corners/cards — user doesn't bet them.)
"""
import json, sqlite3
from datetime import date
from pathlib import Path

import numpy as np
from scipy.stats import poisson

BASE = Path(__file__).parent.parent
DB   = BASE / "database" / "sports_agent.db"

# WC 2026 host countries get a small home-advantage bump; everyone else is neutral.
WC_HOSTS = {"United States", "Canada", "Mexico"}
HOST_BUMP = 1.10  # multiply host expected goals by this

THRESHOLD_HIGH = 65
THRESHOLD_MED  = 50

# Shrinkage strength: pseudo-matches at the tournament average. Higher = ratings pulled
# harder toward the mean (more conservative early in the tournament).
SHRINK_K = 3.0
# Fallback tournament mean goals/team/match before any match is played.
DEFAULT_MU = 1.35


def confidence(score):
    return max(0, min(100, int(round(score * 100))))


def tier(conf):
    if conf >= THRESHOLD_HIGH: return "HIGH"
    if conf >= THRESHOLD_MED:  return "MED"
    return "LOW"


def score_grid(lam, mu, max_goals=10):
    """Independent bivariate Poisson grid P(home=x, away=y)."""
    xs = poisson.pmf(np.arange(max_goals + 1), lam)
    ys = poisson.pmf(np.arange(max_goals + 1), mu)
    grid = np.outer(xs, ys)
    grid /= grid.sum()
    return grid


def predict_1x2(grid):
    p_home = float(np.tril(grid, -1).sum())
    p_draw = float(np.diag(grid).sum())
    p_away = float(np.triu(grid, 1).sum())
    return round(p_home, 4), round(p_draw, 4), round(p_away, 4)


def most_likely_score(grid):
    idx = np.unravel_index(np.argmax(grid), grid.shape)
    return int(idx[0]), int(idx[1]), float(grid[idx])


def build_ratings(conn):
    """Compute per-team attack/defense strengths from finished WC2026 matches.

    Returns (ratings, mu_league, n_matches) where ratings[team] = (A, D) multiplicative
    strengths relative to the tournament mean. Teams with no matches default to (1.0, 1.0).
    """
    rows = conn.execute("""
        SELECT home_team, away_team, home_score, away_score
        FROM matches
        WHERE status='FINISHED'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND home_team IS NOT NULL AND away_team IS NOT NULL
    """).fetchall()

    agg = {}  # team -> [played, gf, ga]
    total_goals = 0
    for home, away, hs, aws in rows:
        hs, aws = int(hs), int(aws)
        total_goals += hs + aws
        for team, gf, ga in ((home, hs, aws), (away, aws, hs)):
            a = agg.setdefault(team, [0, 0, 0])
            a[0] += 1; a[1] += gf; a[2] += ga

    n_matches = len(rows)
    mu = (total_goals / (2 * n_matches)) if n_matches > 0 else DEFAULT_MU

    ratings = {}
    for team, (played, gf, ga) in agg.items():
        att = (gf + SHRINK_K * mu) / (played + SHRINK_K)
        deff = (ga + SHRINK_K * mu) / (played + SHRINK_K)
        ratings[team] = (att / mu, deff / mu)  # (A, D); 1.0 = average

    return ratings, mu, n_matches


def get_rating(ratings, team):
    return ratings.get(team, (1.0, 1.0))


def upsert(conn, match_id, market, pick, conf):
    conn.execute("""
        INSERT INTO predictions (match_id, market, pick, confidence, odds, stake_tier, created_at)
        VALUES (?,?,?,?,0,?,datetime('now'))
        ON CONFLICT(match_id, market) DO UPDATE SET
            pick=excluded.pick,
            confidence=excluded.confidence,
            stake_tier=excluded.stake_tier,
            created_at=excluded.created_at
    """, (match_id, market, pick, conf, tier(conf)))


def write_model_meta(conn, mu, n_matches, teams_rated):
    meta = {
        "model": "WC2026 Poisson + shrinkage",
        "n_wc_matches": n_matches,
        "mu_league": round(mu, 3),
        "k": SHRINK_K,
        "teams_rated": teams_rated,
        "updated": date.today().isoformat(),
    }
    conn.execute("""
        INSERT INTO model_meta (key, json) VALUES ('wc_model', ?)
        ON CONFLICT(key) DO UPDATE SET json=excluded.json
    """, (json.dumps(meta),))


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT, market TEXT, pick TEXT,
            confidence INTEGER, odds REAL, stake_tier TEXT,
            created_at TEXT,
            UNIQUE(match_id, market)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_meta (key TEXT PRIMARY KEY, json TEXT)
    """)
    # Drop deprecated markets one-time.
    conn.execute("DELETE FROM predictions WHERE market IN ('corners','cards','draw_pct')")

    ratings, mu, n_matches = build_ratings(conn)
    print(f"WC2026 model: {n_matches} finished matches  mu={mu:.3f}  teams_rated={len(ratings)}")

    matches = conn.execute("""
        SELECT match_id, home_team, away_team, kickoff_utc
        FROM matches
        WHERE status IN ('SCHEDULED','TIMED')
          AND home_team IS NOT NULL AND away_team IS NOT NULL
          AND date(kickoff_utc) <= date('now', '+40 days')
        ORDER BY kickoff_utc
    """).fetchall()

    ok = 0
    for match_id, home, away, kickoff in matches:
        A_h, D_h = get_rating(ratings, home)
        A_a, D_a = get_rating(ratings, away)

        lam = mu * A_h * D_a
        mu_a = mu * A_a * D_h
        if home in WC_HOSTS: lam *= HOST_BUMP
        if away in WC_HOSTS: mu_a *= HOST_BUMP

        grid = score_grid(lam, mu_a)
        p_h, p_d, p_a = predict_1x2(grid)
        sx, sy, p_score = most_likely_score(grid)

        if p_h > p_a and p_h > p_d:
            winner, conf = home, int(p_h * 100)
        elif p_a > p_h and p_a > p_d:
            winner, conf = away, int(p_a * 100)
        else:
            winner, conf = "Empate", int(p_d * 100)
        upsert(conn, match_id, "winner", winner, conf)

        probs_pick = f"{home} {p_h:.0%} · Empate {p_d:.0%} · {away} {p_a:.0%}"
        upsert(conn, match_id, "probabilities", probs_pick, conf)

        score_pick = f"{sx}-{sy}  (λ={lam:.2f} / μ={mu_a:.2f})"
        upsert(conn, match_id, "scoreline", score_pick, int(p_score * 100))

        print(f"  {home} vs {away}")
        print(f"    P(H)={p_h:.2%}  P(D)={p_d:.2%}  P(A)={p_a:.2%}  -> {winner} {conf}% [{tier(conf)}]")
        print(f"    Marcador probable: {sx}-{sy} ({p_score:.1%})  lam={lam:.2f}  mu={mu_a:.2f}")
        ok += 1

    write_model_meta(conn, mu, n_matches, len(ratings))
    conn.commit()
    conn.close()
    print(f"\nOK={ok}  — predictions written (winner/probabilities/scoreline)")


if __name__ == "__main__":
    main()
