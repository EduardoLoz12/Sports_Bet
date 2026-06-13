"""
Train Dixon-Coles model + Logistic Regression baseline.

Dixon-Coles:
  Each team gets attack (alpha) and defense (beta) parameters.
  Goals_home ~ Poisson(exp(alpha_home - beta_away + gamma_h))
  Goals_away ~ Poisson(exp(alpha_away - beta_home))
  Correction τ handles 0-0, 1-0, 0-1, 1-1 cells.
  Weights: time decay × competition quality.

Evaluation: RPS (Ranked Probability Score) + Brier score.
  Hold-out: most recent 20% of data chronologically.

Output:
  models/dc_params.json        — Dixon-Coles team parameters
  models/logistic_baseline.pkl — sklearn multinomial LR
  models/eval_report.json      — metrics on hold-out set
"""
import json, sqlite3, pickle
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss

BASE   = Path(__file__).parent.parent
DB     = BASE / "database" / "sports_agent.db"
MODELS = BASE / "models"
META   = BASE / "data" / "static" / "team_meta.json"


# ── Dixon-Coles helpers ───────────────────────────────────────────────────────

def dc_tau(x, y, lam, mu, rho):
    """Correction factor for low-scoring cells."""
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    elif x == 0 and y == 1:
        return 1 + lam * rho
    elif x == 1 and y == 0:
        return 1 + mu * rho
    elif x == 1 and y == 1:
        return 1 - rho
    return 1.0


def prep_match_arrays(teams, matches):
    """Pre-index match data into numpy arrays once (avoid per-eval pandas iteration)."""
    t2i = {t: i for i, t in enumerate(teams)}
    hi = matches["home_team"].map(t2i)
    ai = matches["away_team"].map(t2i)
    mask = hi.notna() & ai.notna()

    return {
        "hi":      hi[mask].to_numpy(dtype=np.int64),
        "ai":      ai[mask].to_numpy(dtype=np.int64),
        "x":       matches.loc[mask, "home_goals"].to_numpy(dtype=np.int64),
        "y":       matches.loc[mask, "away_goals"].to_numpy(dtype=np.int64),
        "neutral": matches.loc[mask, "neutral"].to_numpy(dtype=bool),
        "weight":  matches.loc[mask, "total_weight"].to_numpy(dtype=np.float64),
    }


def dc_tau_vec(x, y, lam, mu, rho):
    """Vectorized correction factor for low-scoring cells."""
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


def dc_log_likelihood(params, teams, arrs):
    """Negative weighted log-likelihood for Dixon-Coles (fully vectorized)."""
    n = len(teams)
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

    tau = dc_tau_vec(x, y, lam, mu, rho)
    tau = np.clip(tau, 1e-10, None)

    ll = weight * (np.log(tau) + poisson.logpmf(x, lam) + poisson.logpmf(y, mu))
    return -ll.sum()


def predict_1x2(alpha_h, beta_h, alpha_a, beta_a, gamma, rho,
                neutral=True, max_goals=10):
    """Compute P(home win), P(draw), P(away win) from DC params."""
    lam = np.exp(alpha_h - beta_a + (0.0 if neutral else gamma))
    mu  = np.exp(alpha_a - beta_h)

    grid = np.zeros((max_goals + 1, max_goals + 1))
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            tau = dc_tau(x, y, lam, mu, rho)
            grid[x, y] = max(tau, 0) * poisson.pmf(x, lam) * poisson.pmf(y, mu)

    grid /= grid.sum()  # normalize

    p_home = float(np.tril(grid, -1).sum())
    p_draw = float(np.diag(grid).sum())
    p_away = float(np.triu(grid, 1).sum())
    return p_home, p_draw, p_away


def rps(probs, result):
    """Ranked Probability Score. result: 'H','D','A'."""
    outcome = {"H": [1, 0, 0], "D": [0, 1, 0], "A": [0, 0, 1]}[result]
    cumul_p = np.cumsum(probs)
    cumul_o = np.cumsum(outcome)
    return np.mean((cumul_p - cumul_o) ** 2)


# ── Load data ─────────────────────────────────────────────────────────────────

def load_data():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    df = pd.read_sql("SELECT * FROM training_matches ORDER BY date", conn)
    teams = [r["team"] for r in conn.execute(
        "SELECT team FROM dc_team_index ORDER BY id"
    ).fetchall()]
    conn.close()
    return df, teams


# ── Logistic baseline features ────────────────────────────────────────────────

def build_lr_features(df, meta):
    rows = []
    for _, m in df.iterrows():
        hm = meta.get(m.home_team, {})
        am = meta.get(m.away_team, {})
        if not hm or not am:
            continue

        result = "H" if m.home_goals > m.away_goals else \
                 "D" if m.home_goals == m.away_goals else "A"
        rows.append({
            "diff_elo":       (hm.get("elo_approx", 1700) - am.get("elo_approx", 1700)),
            "diff_rank":      (am.get("fifa_rank", 50) - hm.get("fifa_rank", 50)),
            "diff_wc":        (hm.get("wc_titles", 0) - am.get("wc_titles", 0)),
            "home_is_host":   int(m.home_team in {"United States", "Canada", "Mexico"}),
            "is_neutral":     int(m.neutral),
            "result":         result,
            "weight":         m.total_weight,
        })
    return pd.DataFrame(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    MODELS.mkdir(exist_ok=True)

    print("Loading training data…")
    df, teams = load_data()
    meta = json.loads(META.read_text(encoding="utf-8"))
    print(f"  Matches: {len(df):,}   Teams: {len(teams)}")

    # Chronological train / holdout split (80/20)
    split_idx = int(len(df) * 0.80)
    df_train = df.iloc[:split_idx].copy()
    df_hold  = df.iloc[split_idx:].copy()
    print(f"  Train: {len(df_train):,}   Hold-out: {len(df_hold):,}")
    print(f"  Hold-out from: {df_hold['date'].min()} onward")

    # ── Dixon-Coles ───────────────────────────────────────────────────────────
    print("\nFitting Dixon-Coles…")
    n = len(teams)
    x0 = np.zeros(2 * n + 2)
    x0[2 * n]     = 0.30   # gamma: home advantage ~0.3 goals
    x0[2 * n + 1] = -0.10  # rho: DC correction

    # Pre-index match arrays once (vectorized likelihood needs no per-eval pandas loop)
    train_arrs = prep_match_arrays(teams, df_train)
    print(f"  Indexed matches for MLE: {len(train_arrs['x']):,}")

    # Bounds: rho in (-1, 1)
    bounds = [(None, None)] * (2 * n) + [(None, None)] + [(-0.99, 0.99)]

    result = minimize(
        dc_log_likelihood,
        x0,
        args=(teams, train_arrs),
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 2000, "ftol": 1e-9, "gtol": 1e-7},
    )

    alphas = result.x[:n]
    betas  = result.x[n:2*n]
    gamma  = result.x[2*n]
    rho    = result.x[2*n + 1]

    print(f"  Converged: {result.success}  LL: {-result.fun:.2f}")
    print(f"  gamma (home adv): {gamma:.4f}  rho (DC corr): {rho:.4f}")

    # Save DC params
    dc_params = {
        "teams":   teams,
        "alphas":  alphas.tolist(),
        "betas":   betas.tolist(),
        "gamma":   float(gamma),
        "rho":     float(rho),
        "n_train": len(df_train),
    }
    (MODELS / "dc_params.json").write_text(
        json.dumps(dc_params, indent=2), encoding="utf-8"
    )

    # Top attack / defense teams
    t2a = dict(zip(teams, alphas))
    t2b = dict(zip(teams, betas))
    top_attack  = sorted(t2a.items(), key=lambda x: -x[1])[:10]
    top_defense = sorted(t2b.items(), key=lambda x: -x[1])[:10]
    print("\n  Top 10 attack:  " + ", ".join(f"{t}({a:.2f})" for t, a in top_attack))
    print("  Top 10 defense: " + ", ".join(f"{t}({b:.2f})" for t, b in top_defense))

    # ── Logistic baseline ─────────────────────────────────────────────────────
    print("\nFitting Logistic Regression baseline…")
    lr_df = build_lr_features(df, meta)

    if len(lr_df) < 50:
        print("  Insufficient meta for LR baseline — skipping")
        lr_model = None
    else:
        feats = ["diff_elo", "diff_rank", "diff_wc", "home_is_host", "is_neutral"]
        X_tr  = lr_df.loc[:int(len(lr_df)*0.8), feats].values
        y_tr  = lr_df.loc[:int(len(lr_df)*0.8), "result"].values
        w_tr  = lr_df.loc[:int(len(lr_df)*0.8), "weight"].values

        scaler   = StandardScaler()
        X_tr_sc  = scaler.fit_transform(X_tr)

        lr_model = LogisticRegression(max_iter=500, C=1.0)
        lr_model.fit(X_tr_sc, y_tr, sample_weight=w_tr)

        with open(MODELS / "logistic_baseline.pkl", "wb") as f:
            pickle.dump({"model": lr_model, "scaler": scaler, "features": feats}, f)

        print(f"  LR classes: {lr_model.classes_}  train acc: {lr_model.score(X_tr_sc, y_tr):.3f}")

    # ── Evaluation on hold-out ────────────────────────────────────────────────
    print("\nEvaluating on hold-out set…")
    t2i  = {t: i for i, t in enumerate(teams)}
    rps_dc = []
    brier_dc_h = []

    for _, m in df_hold.iterrows():
        hi = t2i.get(m.home_team)
        ai = t2i.get(m.away_team)
        if hi is None or ai is None:
            continue

        p_h, p_d, p_a = predict_1x2(
            alphas[hi], betas[hi], alphas[ai], betas[ai],
            gamma, rho, neutral=bool(m.neutral)
        )

        actual = "H" if m.home_goals > m.away_goals else \
                 "D" if m.home_goals == m.away_goals else "A"
        rps_dc.append(rps([p_h, p_d, p_a], actual))
        brier_dc_h.append((p_h - (1 if actual == "H" else 0)) ** 2)

    rps_mean   = np.mean(rps_dc)
    brier_mean = np.mean(brier_dc_h)

    # Naive baseline (always predict 45% H / 27% D / 28% A — historical intl averages)
    rps_naive = np.mean([rps([0.45, 0.27, 0.28], "H" if m.home_goals > m.away_goals
                             else "D" if m.home_goals == m.away_goals else "A")
                         for _, m in df_hold.iterrows()])

    print(f"\n  Hold-out: {len(rps_dc)} matches")
    print(f"  DC  RPS:  {rps_mean:.4f}  (lower is better)")
    print(f"  DC  Brier: {brier_mean:.4f}")
    print(f"  Naive RPS: {rps_naive:.4f}")
    print(f"  Skill vs naive: {(1 - rps_mean/rps_naive)*100:.1f}%")

    report = {
        "dc_rps":       rps_mean,
        "dc_brier":     brier_mean,
        "naive_rps":    rps_naive,
        "skill_pct":    (1 - rps_mean / rps_naive) * 100,
        "holdout_n":    len(rps_dc),
        "train_n":      len(df_train),
        "gamma":        float(gamma),
        "rho":          float(rho),
    }
    (MODELS / "eval_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(f"\nModels saved to {MODELS}/")
    print("Next: python tools/predict_wc2026.py")


if __name__ == "__main__":
    main()
