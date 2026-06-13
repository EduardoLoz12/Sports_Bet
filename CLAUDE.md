# CLAUDE.md — World Cup Betting Bot

## WAT Architecture
Workflows → Agents → Tools. AI reasons and orchestrates; Python scripts execute deterministically.

- `workflows/` — Markdown SOPs. Read before doing anything.
- `tools/` — Python scripts. One responsibility each.
- `dashboard/` — Flask web app (bet tracker + P&L).
- `database/` — SQLite. Single source of truth.
- `data/statsbomb/` — Historical StatsBomb open data dumps.
- `.env` — All secrets. Never store credentials elsewhere.
- `.tmp/` — Disposable intermediates only.

---

## Project Goal
Pre-match report bot for 2026 World Cup. Sends Telegram message D-1 before each match with:
- Win probabilities (weighted model)
- Goalscorer picks (anytime, 1H, 2H)
- Corners recommendation (over/under)
- Yellow cards recommendation (over/under)
- Tiered stake suggestions (LOW/MED/HIGH confidence)

Bet results logged to SQLite → P&L dashboard on Flask.

---

## Bet Markets
| Market | Data signals |
|--------|-------------|
| Match winner (1X2) | Head-to-head, form last 10, goal diff, FIFA ranking |
| Anytime scorer | Player goals/90, minutes played, penalty taker status |
| 1H / 2H scorer | Team 1H vs 2H goal split, player timing patterns |
| Corners | Team avg corners for/against last 10, style (possession vs counter) |
| Yellow cards | Team avg cards/game, referee avg cards/game, derby intensity |

---

## Data Sources (free only)
- **api-sports.io** (primary) — Free plan, **100 req/day**, 10 req/min. National team stats 2023-2024. Key: `API_FOOTBALL_KEY`. Auth: `x-apisports-key`. Base: `v3.football.api-sports.io`. Free plan only covers up to season 2024. Uses `/teams/statistics` endpoint (1 call/team). Team ID map cached permanently in `.tmp/team_id_map/` so search costs 0 after first run.
- **football-data.org** (fixtures) — Free tier. WC 2026 fixtures, lineups, scorers. Key: `FOOTBALL_DATA_KEY`. Auth: `X-Auth-Token`. Base: `api.football-data.org/v4`.
- **StatsBomb open data** — GitHub repo `statsbomb/open-data`. Historical WC match events (corners, cards, goals per minute). Cloned to `data/statsbomb/`.

**Rate limit rule:** Cache all API responses locally in `.tmp/YYYY-MM-DD/`. Never re-fetch same endpoint+params twice per day. Sleep 7s between api-sports.io calls.

---

## Prediction Model
**Dixon-Coles ML model** (`tools/train_model.py` + `tools/predict_wc2026.py`) — replaced the old heuristic `scoring_model.py` for the 1X2 winner market.

- Each team gets attack (`alpha`) / defense (`beta`) params fit by MLE (scipy L-BFGS-B) on `training_matches`
- `goals_home ~ Poisson(exp(alpha_home - beta_away + gamma))`, `goals_away ~ Poisson(exp(alpha_away - beta_home))`
- `gamma` = home advantage, `rho` = Dixon-Coles low-score correction (τ factor on 0-0/1-0/0-1/1-1 cells)
- Training data via `tools/collect_training_data.py` — martj42 international results since 2022, competitive matches only, weighted by time-decay (half-life 365d) × competition quality
- Trained on 1,354 matches / 191 teams. Hold-out RPS=0.110 vs naive 0.149 → **+25.9% skill**
- **Vectorize the log-likelihood with numpy** — a per-row pandas `iterrows()` loop makes L-BFGS-B's numerical gradient (≈385 evals/step for 191 teams × 2 params + gamma + rho) take hours; the vectorized version converges in seconds
- `predict_wc2026.py` writes 5 markets per match: `winner`, `probabilities` (full 1X2 split), `scoreline` (Poisson-grid argmax), `corners`, `cards` — corners/cards only emitted when real `team_stats` exist for at least one team (never invented; shows "sin datos" otherwise)
- Models saved to `models/dc_params.json`, `models/eval_report.json`, `models/logistic_baseline.pkl`
- Dashboard `/api/model_info` exposes gamma, rho, RPS, skill% for transparency ("Modelo Predictivo" banner)

Confidence thresholds:
- HIGH ≥ 65 → larger stake tier
- MED 50–64 → medium stake tier
- LOW < 50 → skip or small stake

Retrained daily via cron as part of `scripts/run_daily.sh` (fetch_intl_stats → collect_training_data → train_model → predict_wc2026).

---

## Staking
Tiered by confidence. Exact S/. amounts set by user in `.env`:
```
STAKE_HIGH=
STAKE_MED=
STAKE_LOW=
BANKROLL_START=
```

---

## Hosting
Hetzner VPS (`/opt/sports-agent`, root@5.78.236.186). Cron triggers daily data fetch (6 AM PE time) + D-1 Telegram push (8 PM PE time day before match).

**`/opt/sports-agent` is NOT a git checkout** — plain file-copy deploy. `git pull` there fails ("not a git repository"). Push code changes via sftp/scp.

### Vercel read-only mirror
`EduardoLoz12/Sports_Bet` deployed to Vercel as a read-only dashboard for phone/family access. Hetzner keeps the writable SQLite DB + cron pipeline; Vercel serves Supabase (Postgres, project `jvtmoztlbfxcxzbnedqv`, aws-1-us-east-2).

- `tools/sync_to_supabase.py` — TRUNCATE + re-INSERT (via `psycopg2.extras.execute_values`, batched — `executemany` blows the pooler's 2min `statement_timeout` on the 4000+ row `player_stats` table) of `matches`/`predictions`/`bets`/`team_stats`/`team_extended_stats`/`player_stats`/`model_meta`. Runs at the end of `scripts/run_daily.sh`. Requires `SUPABASE_DB_URL` in Hetzner `.env`.
- `dashboard/db.py` — `get_db()` reads Supabase (Postgres) only when `SUPABASE_DB_URL` is set **and** `VERCEL=1` (Vercel sets this automatically). On Hetzner, `SUPABASE_DB_URL` can be present (as the sync target) without the local dashboard switching off SQLite.
- `api/index.py` + `vercel.json` — Vercel serverless entrypoint, minimal `api/requirements.txt` (flask, python-dotenv, psycopg2-binary — no numpy/pandas/scipy).

---

## Telegram
One message per match. Bot token in `.env`. Never send more than one message per match per day.

---

## Betting Platform
Betano Peru. No official API — odds ingested via manual entry or scraper (TBD).

---

## .env Keys
```
# APIs
API_FOOTBALL_KEY=        # RapidAPI key for API-Football
FOOTBALL_DATA_KEY=       # football-data.org key

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Betano (manual or scraper)
BETANO_USER=
BETANO_PASS=

# Staking
BANKROLL_START=
STAKE_HIGH=
STAKE_MED=
STAKE_LOW=

# Anthropic (optional — for news summarization)
ANTHROPIC_API_KEY=

# Server
PORT=8000
ENV=development

# Supabase (Vercel read-replica sync target + Vercel dashboard read source)
SUPABASE_DB_URL=postgresql://postgres.jvtmoztlbfxcxzbnedqv:<password>@aws-1-us-east-2.pooler.supabase.com:6543/postgres
```

---

## Database Schema (sports_agent.db)

```sql
CREATE TABLE matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id TEXT UNIQUE,           -- API-Football match ID
  home_team TEXT,
  away_team TEXT,
  kickoff_utc DATETIME,
  group_stage TEXT,
  stage TEXT,                     -- "group" | "r16" | "qf" | "sf" | "final"
  status TEXT DEFAULT "scheduled"
);

CREATE TABLE predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id TEXT,
  market TEXT,                    -- "winner" | "probabilities" | "scoreline" | "corners" | "cards"
  pick TEXT,
  confidence INTEGER,             -- 0-100
  odds REAL,
  stake_tier TEXT,                -- "HIGH" | "MED" | "LOW"
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(match_id, market)        -- one row per market per match; predict_wc2026.py
                                   -- upserts on (match_id, market), overwriting `pick`.
                                   -- NEVER (match_id, market, pick) — pick text embeds
                                   -- live %/lambda/mu that shift each retrain, so that
                                   -- constraint silently INSERTs duplicates instead of
                                   -- updating (caused dashboard dup-prediction bug 2026-06-10).
);

CREATE TABLE bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id TEXT,
  market TEXT,
  pick TEXT,
  odds REAL,
  stake_soles REAL,
  result TEXT,                    -- "win" | "loss" | "void" | "pending"
  profit_soles REAL,
  placed_at DATETIME,
  settled_at DATETIME
);

CREATE TABLE team_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT,
  stat_date TEXT,
  avg_corners_for REAL,
  avg_corners_against REAL,
  avg_cards REAL,
  goals_1h REAL,
  goals_2h REAL,
  form_last10 TEXT,               -- JSON array of W/D/L
  fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## Workflow Sequence (daily)
1. `fetch_fixtures.py` — get next 48h WC matches
2. `fetch_team_stats.py` — update team stats for those teams
3. `fetch_player_stats.py` — update player goal/card stats
4. `fetch_intl_stats.py` — update extended team stats (FIFA rank, ELO, form, confederation) from martj42
5. `collect_training_data.py` — rebuild `training_matches` table from martj42 results (time-decay + competition weighting)
6. `train_model.py` — fit Dixon-Coles params (alpha/beta/gamma/rho) via MLE, write `models/dc_params.json` + `eval_report.json`
7. `predict_wc2026.py` — score upcoming matches with the trained model, write `winner`/`probabilities`/`scoreline`/`corners`/`cards` to `predictions`
   - Only scores matches with `home_team`/`away_team` NOT NULL. Knockout-stage fixtures (R16/QF/SF/Final, ~62 of 104) come from football-data.org with NULL team names until the group stage resolves the bracket — this is EXPECTED, not a bug. They populate automatically via `fetch_fixtures.py`'s upsert as groups finish, and predictions appear on the next daily cron run (all knockout matches fall within the `+40 days` window).
   - `corners`/`cards` only written when real `team_stats` exists for at least one team — matches without it get 3 markets (`winner`/`probabilities`/`scoreline`), matches with it get 5. Dashboard shows ALL rows from `predictions` for a match (no confidence filter) — 3 vs 5 cards on the dashboard reflects `team_stats` coverage only, not a display bug.
8. `generate_report.py` — build Telegram message text
9. `telegram_send.py` — push to user

All steps run via `bash scripts/run_daily.sh` (cron: `0 11 * * *` = 6 AM PE time).

## Bet Logging (manual trigger)
`log_bet.py` — user inputs match + market + odds + stake → written to `bets` table.

---

## API Cost Rules
Cache everything. Never call same endpoint twice same day. Log all API calls with timestamp.
