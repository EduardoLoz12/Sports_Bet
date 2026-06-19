"""
Dixon-Coles predictions for WC 2026 matches.
Replaces scoring_model.py — uses trained model params, not hand-tuned weights.

For each upcoming match:
  P(home win), P(draw), P(away win) from Poisson grid.
  Confidence = max(P_home, P_away) * 100 if clear winner, else draw conf.
  Stake tier: HIGH >= 65%, MED >= 50%, LOW < 50%.
"""
import json, sqlite3
from pathlib import Path

import numpy as np
from scipy.stats import poisson

BASE   = Path(__file__).parent.parent
DB     = BASE / "database" / "sports_agent.db"
MODELS = BASE / "models"

# WC 2026 host countries
WC_HOSTS = {"United States", "Canada", "Mexico"}

# Name normalization: football-data.org → martj42 (same mapping as fetch_intl_stats)
FD_TO_M42 = {
    "United States":       "United States",
    "South Korea":         "South Korea",
    "Congo DR":            "DR Congo",
    "Ivory Coast":         "Ivory Coast",
    "Bosnia-Herzegovina":  "Bosnia and Herzegovina",
    "Czechia":             "Czech Republic",
}

THRESHOLD_HIGH = 65
THRESHOLD_MED  = 50

DEFAULT_CORNERS_LINE = 9.5
DEFAULT_CARDS_LINE   = 3.5

# WC2026 referee directive favors leniency on bookings vs club football —
# discount historical team card averages (pre-tournament season data) accordingly.
CARDS_LENIENCY_FACTOR = 0.80


def dc_tau(x, y, lam, mu, rho):
    if   x == 0 and y == 0: return 1 - lam * mu * rho
    elif x == 0 and y == 1: return 1 + lam * rho
    elif x == 1 and y == 0: return 1 + mu * rho
    elif x == 1 and y == 1: return 1 - rho
    return 1.0


def score_grid(alpha_h, beta_h, alpha_a, beta_a, gamma, rho,
               neutral=True, max_goals=10):
    """Full Poisson grid P(home_goals=x, away_goals=y), Dixon-Coles corrected."""
    lam = np.exp(alpha_h - beta_a + (0.0 if neutral else gamma))
    mu  = np.exp(alpha_a - beta_h)

    grid = np.zeros((max_goals + 1, max_goals + 1))
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            tau = dc_tau(x, y, lam, mu, rho)
            grid[x, y] = max(tau, 0) * poisson.pmf(x, lam) * poisson.pmf(y, mu)

    grid /= grid.sum()
    return grid, lam, mu


def predict_1x2(grid):
    p_home = float(np.tril(grid, -1).sum())
    p_draw = float(np.diag(grid).sum())
    p_away = float(np.triu(grid, 1).sum())
    return round(p_home, 4), round(p_draw, 4), round(p_away, 4)


def most_likely_score(grid):
    idx = np.unravel_index(np.argmax(grid), grid.shape)
    return int(idx[0]), int(idx[1]), float(grid[idx])


def confidence(score):
    return max(0, min(100, int(round(score * 100))))


def get_team_stats(conn, team_id):
    row = conn.execute("""
        SELECT * FROM team_stats WHERE team_id=? ORDER BY stat_date DESC LIMIT 1
    """, (team_id,)).fetchone()
    return dict(row) if row else {}


def predict_corners(home_stats, away_stats):
    home_for     = home_stats.get("avg_corners_for") or 5.0
    home_against = home_stats.get("avg_corners_against") or 5.0
    away_for     = away_stats.get("avg_corners_for") or 5.0
    away_against = away_stats.get("avg_corners_against") or 5.0

    if not home_stats.get("avg_corners_for"):
        home_for = (home_stats.get("goals_scored_avg") or 1.0) * 3.5
    if not away_stats.get("avg_corners_for"):
        away_for = (away_stats.get("goals_scored_avg") or 1.0) * 3.5

    expected = (home_for + away_against + away_for + home_against) / 2
    line = DEFAULT_CORNERS_LINE

    if expected > line + 1:
        return f"sobre {line}", confidence(min((expected - line) / 3, 1.0))
    if expected < line - 1:
        return f"bajo {line}", confidence(min((line - expected) / 3, 1.0))
    return f"sobre {line}", confidence(0.45)


def predict_cards(home_stats, away_stats):
    home_cards = home_stats.get("avg_cards") or 0.0
    away_cards = away_stats.get("avg_cards") or 0.0
    line = DEFAULT_CARDS_LINE

    if home_cards == 0.0 and away_cards == 0.0:
        return f"bajo {line} (sin datos)", 35

    expected = ((home_cards or 2.0) + (away_cards or 2.0)) * CARDS_LENIENCY_FACTOR
    if expected > line + 0.5:
        return f"sobre {line}", confidence(min((expected - line) / 2, 1.0))
    if expected < line - 0.5:
        return f"bajo {line}", confidence(min((line - expected) / 2, 1.0))
    return f"sobre {line}", confidence(0.45)


def tier(conf):
    if conf >= THRESHOLD_HIGH: return "HIGH"
    if conf >= THRESHOLD_MED:  return "MED"
    return "LOW"


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


def main():
    # Load DC params
    params_file = MODELS / "dc_params.json"
    if not params_file.exists():
        print("ERROR: models/dc_params.json not found. Run train_model.py first.")
        return

    dc = json.loads(params_file.read_text(encoding="utf-8"))
    teams  = dc["teams"]
    alphas = np.array(dc["alphas"])
    betas  = np.array(dc["betas"])
    gamma  = dc["gamma"]
    rho    = dc["rho"]
    t2i    = {t: i for i, t in enumerate(teams)}

    eval_report = {}
    eval_file = MODELS / "eval_report.json"
    if eval_file.exists():
        eval_report = json.loads(eval_file.read_text(encoding="utf-8"))

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

    conn.execute("DELETE FROM predictions WHERE market='draw_pct'")

    matches = conn.execute("""
        SELECT match_id, home_team, away_team, home_team_id, away_team_id, kickoff_utc
        FROM matches
        WHERE status IN ('SCHEDULED','TIMED')
          AND home_team IS NOT NULL AND away_team IS NOT NULL
          AND date(kickoff_utc) <= date('now', '+40 days')
        ORDER BY kickoff_utc
    """).fetchall()

    print(f"DC params: {len(teams)} teams  gamma={gamma:.3f}  rho={rho:.3f}")
    if eval_report:
        print(f"Model skill: {eval_report.get('skill_pct',0):.1f}% vs naive | "
              f"RPS={eval_report.get('dc_rps',0):.4f}")
    print()

    ok = skip = 0
    for match_id, home, away, home_id, away_id, kickoff in matches:
        # Normalize team names to martj42 convention for lookup
        home_m42 = FD_TO_M42.get(home, home)
        away_m42 = FD_TO_M42.get(away, away)

        hi = t2i.get(home_m42)
        ai = t2i.get(away_m42)

        if hi is None and ai is None:
            print(f"  SKIP {home} vs {away} — neither team in model")
            skip += 1
            continue

        # For teams missing from model: use confederation average
        def safe_alpha(idx, team_name):
            if idx is not None:
                return alphas[idx]
            return alphas.mean()  # prior: average attack

        def safe_beta(idx, team_name):
            if idx is not None:
                return betas[idx]
            return betas.mean()  # prior: average defense

        alpha_h = safe_alpha(hi, home_m42)
        beta_h  = safe_beta(hi, home_m42)
        alpha_a = safe_alpha(ai, away_m42)
        beta_a  = safe_beta(ai, away_m42)

        # WC 2026: USA/CAN/MEX have home advantage, rest is neutral
        is_neutral = home not in WC_HOSTS

        grid, lam, mu = score_grid(alpha_h, beta_h, alpha_a, beta_a,
                                   gamma, rho, neutral=is_neutral)
        p_h, p_d, p_a = predict_1x2(grid)
        sx, sy, p_score = most_likely_score(grid)

        partial = hi is None or ai is None
        tag = " [partial]" if partial else ""

        # 1) Winner
        if p_h > p_a and p_h > p_d:
            winner, conf = home, int(p_h * 100)
        elif p_a > p_h and p_a > p_d:
            winner, conf = away, int(p_a * 100)
        else:
            winner, conf = "Draw", int(p_d * 100)
        upsert(conn, match_id, "winner", winner, conf)

        # 2) Full 1X2 probability breakdown — model detail for the card
        probs_pick = f"{home} {p_h:.0%} · Empate {p_d:.0%} · {away} {p_a:.0%}"
        upsert(conn, match_id, "probabilities", probs_pick, conf)

        # 3) Most likely scoreline (Dixon-Coles Poisson grid argmax)
        score_pick = f"{sx}-{sy}  (λ={lam:.2f} / μ={mu:.2f})"
        upsert(conn, match_id, "scoreline", score_pick, int(p_score * 100))

        # 4) Corners & 5) Cards — from real team_stats (api-sports.io)
        hstats = get_team_stats(conn, home_id) if home_id else {}
        astats = get_team_stats(conn, away_id) if away_id else {}
        if hstats or astats:
            corners_pick, corners_conf = predict_corners(hstats, astats)
            upsert(conn, match_id, "corners", corners_pick, corners_conf)

            cards_pick, cards_conf = predict_cards(hstats, astats)
            upsert(conn, match_id, "cards", cards_pick, cards_conf)

        print(f"  {home} vs {away}{tag}")
        print(f"    P(H)={p_h:.2%}  P(D)={p_d:.2%}  P(A)={p_a:.2%}  -> {winner} {conf}% [{tier(conf)}]")
        print(f"    Marcador probable: {sx}-{sy} ({p_score:.1%})  lambda={lam:.2f}  mu={mu:.2f}")
        ok += 1

    conn.commit()
    conn.close()
    print(f"\nOK={ok}  SKIP={skip}  — predictions written to DB")


if __name__ == "__main__":
    main()
