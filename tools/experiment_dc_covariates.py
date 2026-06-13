"""
Experiment: does adding ELO / FIFA-rank covariates to Dixon-Coles improve
hold-out RPS vs the current alpha/beta-only model?

Extended model:
  lam = exp(alpha_h - beta_a + gamma*(1-neutral) + d_elo*elo_diff + d_rank*rank_diff)
  mu  = exp(alpha_a - beta_h               - d_elo*elo_diff - d_rank*rank_diff)

  elo_diff  = (elo_home - elo_away) / 400      (scaled like chess Elo expected score)
  rank_diff = (rank_away - rank_home) / 50     (positive favors home, i.e. home ranked better)

Teams missing from team_meta.json get neutral defaults (elo=1700, rank=50) —
same defaults used by the existing logistic baseline in train_model.py.

Read-only experiment. Does not touch models/dc_params.json.
"""
import json, sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

BASE = Path(__file__).parent.parent
DB   = BASE / "database" / "sports_agent.db"
META = BASE / "data" / "static" / "team_meta.json"


def dc_tau_vec(x, y, lam, mu, rho):
    tau = np.ones_like(lam)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    tau = np.where(m00, 1 - lam * mu * rho, tau)
    tau = np.where(m01, 1 + lam * rho, tau)
    tau = np.where(m10, 1 + mu * rho, tau)
    tau = np.where(m11, 1 - rho, tau)
    return tau


def dc_tau(x, y, lam, mu, rho):
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    elif x == 0 and y == 1:
        return 1 + lam * rho
    elif x == 1 and y == 0:
        return 1 + mu * rho
    elif x == 1 and y == 1:
        return 1 - rho
    return 1.0


def predict_1x2(alpha_h, beta_h, alpha_a, beta_a, gamma, rho,
                neutral, elo_term=0.0, rank_term=0.0, max_goals=10):
    lam = np.exp(alpha_h - beta_a + (0.0 if neutral else gamma) + elo_term + rank_term)
    mu  = np.exp(alpha_a - beta_h - elo_term - rank_term)

    grid = np.zeros((max_goals + 1, max_goals + 1))
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            tau = dc_tau(x, y, lam, mu, rho)
            grid[x, y] = max(tau, 0) * poisson.pmf(x, lam) * poisson.pmf(y, mu)
    grid /= grid.sum()

    p_home = float(np.tril(grid, -1).sum())
    p_draw = float(np.diag(grid).sum())
    p_away = float(np.triu(grid, 1).sum())
    return p_home, p_draw, p_away


def rps(probs, result):
    outcome = {"H": [1, 0, 0], "D": [0, 1, 0], "A": [0, 0, 1]}[result]
    cumul_p = np.cumsum(probs)
    cumul_o = np.cumsum(outcome)
    return np.mean((cumul_p - cumul_o) ** 2)


def prep_match_arrays(teams, matches, meta):
    t2i = {t: i for i, t in enumerate(teams)}
    hi = matches["home_team"].map(t2i)
    ai = matches["away_team"].map(t2i)
    mask = hi.notna() & ai.notna()
    sub = matches[mask]

    elo_h  = sub["home_team"].map(lambda t: meta.get(t, {}).get("elo_approx", 1700)).to_numpy(dtype=np.float64)
    elo_a  = sub["away_team"].map(lambda t: meta.get(t, {}).get("elo_approx", 1700)).to_numpy(dtype=np.float64)
    rank_h = sub["home_team"].map(lambda t: meta.get(t, {}).get("fifa_rank", 50)).to_numpy(dtype=np.float64)
    rank_a = sub["away_team"].map(lambda t: meta.get(t, {}).get("fifa_rank", 50)).to_numpy(dtype=np.float64)

    return {
        "hi":      hi[mask].to_numpy(dtype=np.int64),
        "ai":      ai[mask].to_numpy(dtype=np.int64),
        "x":       sub["home_goals"].to_numpy(dtype=np.int64),
        "y":       sub["away_goals"].to_numpy(dtype=np.int64),
        "neutral": sub["neutral"].to_numpy(dtype=bool),
        "weight":  sub["total_weight"].to_numpy(dtype=np.float64),
        "elo_diff":  (elo_h - elo_a) / 400.0,
        "rank_diff": (rank_a - rank_h) / 50.0,
    }


# ── Baseline log-likelihood (alpha, beta, gamma, rho) ──────────────────────

def ll_baseline(params, n, arrs):
    alphas = params[:n]
    betas  = params[n:2*n]
    gamma  = params[2*n]
    rho    = params[2*n + 1]

    hi, ai = arrs["hi"], arrs["ai"]
    x, y   = arrs["x"], arrs["y"]
    neutral, weight = arrs["neutral"], arrs["weight"]

    home_adv = np.where(neutral, 0.0, gamma)
    lam = np.exp(alphas[hi] - betas[ai] + home_adv)
    mu  = np.exp(alphas[ai] - betas[hi])

    tau = np.clip(dc_tau_vec(x, y, lam, mu, rho), 1e-10, None)
    ll = weight * (np.log(tau) + poisson.logpmf(x, lam) + poisson.logpmf(y, mu))
    return -ll.sum()


# ── Extended log-likelihood (+ delta_elo, delta_rank, or either) ───────────

def make_ll_extended(use_elo, use_rank):
    def ll(params, n, arrs):
        alphas = params[:n]
        betas  = params[n:2*n]
        gamma  = params[2*n]
        rho    = params[2*n + 1]
        i = 2*n + 2
        d_elo  = params[i] if use_elo else 0.0
        i += 1 if use_elo else 0
        d_rank = params[i] if use_rank else 0.0

        hi, ai = arrs["hi"], arrs["ai"]
        x, y   = arrs["x"], arrs["y"]
        neutral, weight = arrs["neutral"], arrs["weight"]

        home_adv = np.where(neutral, 0.0, gamma)
        cov = d_elo * arrs["elo_diff"] + d_rank * arrs["rank_diff"]
        lam = np.exp(alphas[hi] - betas[ai] + home_adv + cov)
        mu  = np.exp(alphas[ai] - betas[hi] - cov)

        tau = np.clip(dc_tau_vec(x, y, lam, mu, rho), 1e-10, None)
        ll_val = weight * (np.log(tau) + poisson.logpmf(x, lam) + poisson.logpmf(y, mu))
        return -ll_val.sum()
    return ll


def fit(loss_fn, n, arrs, n_extra):
    x0 = np.zeros(2 * n + 2 + n_extra)
    x0[2 * n]     = 0.30
    x0[2 * n + 1] = -0.10
    bounds = [(None, None)] * (2 * n) + [(None, None)] + [(-0.99, 0.99)] + [(None, None)] * n_extra
    result = minimize(loss_fn, x0, args=(n, arrs), method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 8000, "maxfun": 40000, "ftol": 1e-12, "gtol": 1e-9})
    return result


def evaluate(df_hold, teams, alphas, betas, gamma, rho, meta, d_elo=0.0, d_rank=0.0):
    t2i = {t: i for i, t in enumerate(teams)}
    rps_list, brier_list = [], []
    for _, m in df_hold.iterrows():
        hi = t2i.get(m.home_team)
        ai = t2i.get(m.away_team)
        if hi is None or ai is None:
            continue

        elo_h  = meta.get(m.home_team, {}).get("elo_approx", 1700)
        elo_a  = meta.get(m.away_team, {}).get("elo_approx", 1700)
        rank_h = meta.get(m.home_team, {}).get("fifa_rank", 50)
        rank_a = meta.get(m.away_team, {}).get("fifa_rank", 50)
        elo_term  = d_elo  * (elo_h - elo_a) / 400.0
        rank_term = d_rank * (rank_a - rank_h) / 50.0

        p_h, p_d, p_a = predict_1x2(
            alphas[hi], betas[hi], alphas[ai], betas[ai],
            gamma, rho, neutral=bool(m.neutral),
            elo_term=elo_term, rank_term=rank_term,
        )
        actual = "H" if m.home_goals > m.away_goals else \
                 "D" if m.home_goals == m.away_goals else "A"
        rps_list.append(rps([p_h, p_d, p_a], actual))
        brier_list.append((p_h - (1 if actual == "H" else 0)) ** 2)
    return np.mean(rps_list), np.mean(brier_list), len(rps_list)


def main():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT * FROM training_matches ORDER BY date", conn)
    teams = [r[0] for r in conn.execute("SELECT team FROM dc_team_index ORDER BY id").fetchall()]
    conn.close()
    meta = json.loads(META.read_text(encoding="utf-8"))
    n = len(teams)

    split_idx = int(len(df) * 0.80)
    df_train = df.iloc[:split_idx].copy()
    df_hold  = df.iloc[split_idx:].copy()
    print(f"Train: {len(df_train):,}  Hold-out: {len(df_hold):,}  Teams: {n}")

    arrs = prep_match_arrays(teams, df_train, meta)

    # naive baseline
    rps_naive = np.mean([
        rps([0.45, 0.27, 0.28], "H" if m.home_goals > m.away_goals
            else "D" if m.home_goals == m.away_goals else "A")
        for _, m in df_hold.iterrows()
    ])

    results = {}

    # ── Baseline DC ──
    print("\nFitting baseline DC (alpha, beta, gamma, rho)...")
    r0 = fit(ll_baseline, n, arrs, n_extra=0)
    a0, b0 = r0.x[:n], r0.x[n:2*n]
    g0, rho0 = r0.x[2*n], r0.x[2*n+1]
    rps0, brier0, k0 = evaluate(df_hold, teams, a0, b0, g0, rho0, meta)
    print(f"  converged={r0.success}  gamma={g0:.4f}  rho={rho0:.4f}")
    print(f"  RPS={rps0:.4f}  Brier={brier0:.4f}  n={k0}  skill={(1-rps0/rps_naive)*100:.2f}%")
    results["baseline"] = rps0

    # ── Ablations: elo-only, rank-only, both ──
    for label, use_elo, use_rank in [("elo-only", True, False), ("rank-only", False, True), ("elo+rank", True, True)]:
        n_extra = int(use_elo) + int(use_rank)
        print(f"\nFitting extended DC ({label})...")
        r = fit(make_ll_extended(use_elo, use_rank), n, arrs, n_extra=n_extra)
        a1, b1 = r.x[:n], r.x[n:2*n]
        g1, rho1 = r.x[2*n], r.x[2*n+1]
        i = 2*n + 2
        d_elo = r.x[i] if use_elo else 0.0
        i += 1 if use_elo else 0
        d_rank = r.x[i] if use_rank else 0.0
        rps1, brier1, k1 = evaluate(df_hold, teams, a1, b1, g1, rho1, meta, d_elo, d_rank)
        print(f"  converged={r.success}  gamma={g1:.4f}  rho={rho1:.4f}")
        print(f"  delta_elo={d_elo:.4f}  delta_rank={d_rank:.4f}")
        print(f"  RPS={rps1:.4f}  Brier={brier1:.4f}  n={k1}  skill={(1-rps1/rps_naive)*100:.2f}%")
        results[label] = rps1

    print("\n--- Summary (lower RPS = better) ---")
    print(f"  Naive RPS:    {rps_naive:.4f}")
    for label, r in results.items():
        skill = (1 - r/rps_naive)*100
        delta = results["baseline"] - r
        marker = "" if label == "baseline" else f"   delta vs baseline: {delta:+.5f}"
        print(f"  {label:10s} RPS: {r:.4f}  (skill {skill:.2f}%){marker}")


if __name__ == "__main__":
    main()
