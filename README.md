# Sports Bet Agent — World Cup 2026 (v2.0.0)

**Decision-support dashboard for real bets.** The bot can't connect to the betting house, so it tracks
NO money — it surfaces, per WC2026 match, everything to look at before betting: model win
probabilities + probable scoreline (computed **only from WC2026 results**), Reddit/GNews crowd
sentiment, and per-team tournament stats (group position, est. points-to-qualify, top scorer, team
goals/assists, past opponents + results, remaining fixtures). Daily cron refreshes data on Hetzner;
a read-only Supabase mirror powers the Vercel dashboard.

> **v2.0.0 changes:** removed money/profit/ROI/bankroll tracking (`bets` table + `log_bet.py`),
> dropped corners & cards markets, replaced the historical Dixon-Coles model with a WC2026-only
> Poisson+shrinkage model, and added the tournament stat panels.

## Architecture: WAT (Workflows → Agents → Tools)

AI reasons and orchestrates; Python scripts execute deterministically.

```
workflows/   Markdown SOPs — read these first
tools/       Python scripts, one responsibility each
dashboard/   Flask app — pre-bet analysis (no money tracking)
database/    SQLite (sports_agent.db) — single source of truth
data/        StatsBomb open data + static team metadata
scripts/     Cron/deploy entrypoints
```

## Prediction model (WC2026-only Poisson + shrinkage)

Computed inside `tools/predict_wc2026.py` directly from finished WC2026 matches — no historical
data, no MLE training step.

- Per team: attack/defense rates from WC goals for/against, shrunk toward the tournament mean
  (`k=3` pseudo-matches) so teams with 1-2 games don't swing wildly
- `lam = mu * A_home * D_away`, `mu_a = mu * A_away * D_home`; hosts (USA/CAN/MEX) get a 1.10 bump
- Independent bivariate Poisson grid → 1X2 probabilities + most-likely scoreline
- Outputs 3 markets per match: `winner`, `probabilities`, `scoreline`
- `/api/model_info` exposes `n_wc_matches`, `mu_league`, `k`, `teams_rated`

## Markets

| Market | Signals |
|---|---|
| Match winner (1X2) | WC2026 attack/defense ratings (Poisson + shrinkage) |
| Probabilities (1X2) | Same Poisson grid |
| Probable scoreline | Poisson-grid argmax |

Confidence tiers (display only, no staking): HIGH ≥ 65, MED 50-64, LOW < 50.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
```

### Required `.env` keys

```
API_FOOTBALL_KEY=       # api-sports.io (100 req/day free)
FOOTBALL_DATA_KEY=      # football-data.org (fixtures)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
BANKROLL_START=
STAKE_HIGH=
STAKE_MED=
STAKE_LOW=
ANTHROPIC_API_KEY=      # optional, news summarization
PORT=8000
ENV=development
```

## Daily pipeline

```bash
bash scripts/run_daily.sh
```

Runs: `fetch_fixtures.py` → `fetch_team_stats.py` → `fetch_player_stats.py` → `fetch_intl_stats.py` → `collect_training_data.py` → `train_model.py` → `predict_wc2026.py` → `generate_report.py` → `telegram_send.py`

Scheduled via cron on a Hetzner VPS: fetch at 6 AM PE, Telegram push at 8 PM PE (D-1).

## Dashboard

```bash
python dashboard/app.py
```

Flask bet tracker + P&L view, including a "Modelo Predictivo" panel exposing gamma, rho, RPS and skill%.

### Deploying the dashboard to Vercel (read-only mirror)

The Hetzner box keeps owning the cron pipeline + the writable SQLite DB.
Vercel serves a read-only copy of the dashboard, fed by a Supabase (Postgres)
replica that `tools/sync_to_supabase.py` pushes after every daily run.

Supabase project is already created and seeded (schema applied,
`SUPABASE_DB_URL` set in Hetzner `.env`, synced via `tools/sync_to_supabase.py`
— runs automatically at the end of `run_daily.sh`).

**Remaining step — on Vercel**, import `EduardoLoz12/Sports_Bet` (it
auto-detects `vercel.json` / `api/index.py`). In Project Settings →
Environment Variables add:
```
SUPABASE_DB_URL=postgresql://postgres.jvtmoztlbfxcxzbnedqv:<password>@aws-1-us-east-2.pooler.supabase.com:6543/postgres
BANKROLL_START=...
STAKE_HIGH=...
STAKE_MED=...
STAKE_LOW=...
```
(password from Hetzner `.env`). Deploy. `dashboard/db.py` switches to the
Supabase replica when `SUPABASE_DB_URL` is set AND `VERCEL=1` (Vercel sets
this automatically) — so Hetzner can also hold `SUPABASE_DB_URL` (as the sync
target) without its own dashboard switching off local SQLite.

## Data sources (all free tier)

- **api-sports.io** — national team stats (2023-2024 seasons)
- **football-data.org** — WC 2026 fixtures, lineups, scorers
- **StatsBomb open data** — historical WC match events for corners/cards baselines

All API responses are cached locally per day; no endpoint is called twice with the same params on the same day.

## Disclaimer

For personal research and analysis only. Not financial advice — bet responsibly.
