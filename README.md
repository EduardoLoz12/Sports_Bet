# Sports Bet Agent — World Cup 2026

Pre-match betting analysis bot for the 2026 World Cup. Every day it pulls fresh team/player data, retrains a Dixon-Coles model, scores upcoming matches, and pushes a Telegram report (D-1 before each match) with win probabilities, goalscorer picks, corners/cards recommendations and tiered stake suggestions. Results are logged to SQLite and tracked on a Flask dashboard.

## Architecture: WAT (Workflows → Agents → Tools)

AI reasons and orchestrates; Python scripts execute deterministically.

```
workflows/   Markdown SOPs — read these first
tools/       Python scripts, one responsibility each
dashboard/   Flask app — bet tracker & P&L
database/    SQLite (sports_agent.db) — single source of truth
data/        StatsBomb open data + static team metadata
models/      Trained Dixon-Coles params & eval reports (generated)
scripts/     Cron/deploy entrypoints
```

## Prediction model

Dixon-Coles Poisson model (`tools/train_model.py` / `tools/predict_wc2026.py`), replacing an earlier heuristic scorer.

- Each team gets attack (`alpha`) and defense (`beta`) ratings fit by MLE (scipy L-BFGS-B)
- `goals_home ~ Poisson(exp(alpha_home - beta_away + gamma))`
- `gamma` = home advantage, `rho` = Dixon-Coles low-score correction
- Trained on 1,354 international matches / 191 teams, time-decay weighted (half-life 365d)
- Hold-out RPS = 0.110 vs naive 0.149 → **+25.9% skill**
- Outputs 5 markets per match: `winner`, `probabilities`, `scoreline`, `corners`, `cards` (corners/cards only when real team stats are available — never invented)

## Betting markets

| Market | Signals |
|---|---|
| Match winner (1X2) | Head-to-head, form last 10, goal diff, FIFA ranking |
| Anytime scorer | Goals/90, minutes played, penalty-taker status |
| 1H / 2H scorer | Team half-split goal timing |
| Corners | Avg corners for/against last 10, playing style |
| Yellow cards | Team & referee card averages, derby intensity |

Stakes are tiered by model confidence (HIGH ≥ 65, MED 50-64, LOW < 50), with S/. amounts configured in `.env`.

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

**1. Create the Supabase project (one-time, via supabase.com dashboard):**
- New project (free tier)
- SQL Editor → paste `supabase/schema.sql` → Run
- Project Settings → Database → Connection string → URI (pooler, port 6543) → this is `SUPABASE_DB_URL`

**2. On Hetzner**, add to `.env`:
```
SUPABASE_DB_URL=postgresql://postgres.xxxx:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
```
Run `python3 tools/sync_to_supabase.py` once to seed it (it then runs automatically at the end of `run_daily.sh`).

**3. On Vercel**, import `EduardoLoz12/Sports_Bet` (it auto-detects `vercel.json` / `api/index.py`). In Project Settings → Environment Variables add:
```
SUPABASE_DB_URL=postgresql://postgres.xxxx:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
BANKROLL_START=...
STAKE_HIGH=...
STAKE_MED=...
STAKE_LOW=...
```
Deploy. `dashboard/db.py` automatically switches from local SQLite to the Supabase replica whenever `SUPABASE_DB_URL` is set.

## Data sources (all free tier)

- **api-sports.io** — national team stats (2023-2024 seasons)
- **football-data.org** — WC 2026 fixtures, lineups, scorers
- **StatsBomb open data** — historical WC match events for corners/cards baselines

All API responses are cached locally per day; no endpoint is called twice with the same params on the same day.

## Disclaimer

For personal research and analysis only. Not financial advice — bet responsibly.
