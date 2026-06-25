# CLAUDE.md — World Cup Betting Bot (v2.0.0)

## WAT Architecture
Workflows → Agents → Tools. AI reasons and orchestrates; Python scripts execute deterministically.

- `workflows/` — Markdown SOPs. Read before doing anything.
- `tools/` — Python scripts. One responsibility each.
- `dashboard/` — Flask web app (pre-bet analysis dashboard — NO money tracking).
- `database/` — SQLite. Single source of truth.
- `data/statsbomb/` — Historical StatsBomb open data dumps.
- `.env` — All secrets. Never store credentials elsewhere.
- `.tmp/` — Disposable intermediates only.

---

## Project Goal (v2.0.0)
**Decision-support tool for the user's REAL bets** — the bot can't connect to Betano, so it tracks
NO money/profit. It surfaces, per WC2026 match, everything needed before betting:
- Win probabilities + probable scoreline (model, **ONLY from WC2026 results**)
- Reddit/GNews crowd sentiment
- Per-team WC2026 stats: group position, est. points-to-qualify, top scorer, team goals/assists,
  past opponents + results, remaining fixtures

**v2.0.0 removed:** money/profit/ROI/bankroll KPIs, `bets` table, `log_bet.py`, corners & cards
markets, and the Dixon-Coles model trained on historical martj42 data.

---

## Bet Markets (v2.0.0)
| Market | Data signals |
|--------|-------------|
| Match winner (1X2) | WC2026 attack/defense ratings (Poisson + shrinkage) |
| Probabilities (full 1X2) | Same Poisson grid |
| Probable scoreline | Poisson-grid argmax |

Corners & yellow-card markets were **removed** in v2.0.0 (user doesn't bet them).

---

## Data Sources (free only)
- **api-sports.io** (primary) — Free plan, **100 req/day**, 10 req/min. National team stats 2023-2024. Key: `API_FOOTBALL_KEY`. Auth: `x-apisports-key`. Base: `v3.football.api-sports.io`. Free plan only covers up to season 2024. Uses `/teams/statistics` endpoint (1 call/team). Team ID map cached permanently in `.tmp/team_id_map/` so search costs 0 after first run.
- **football-data.org** (fixtures) — Free tier. WC 2026 fixtures, lineups, scorers. Key: `FOOTBALL_DATA_KEY`. Auth: `X-Auth-Token`. Base: `api.football-data.org/v4`.
- **StatsBomb open data** — GitHub repo `statsbomb/open-data`. Historical WC match events (corners, cards, goals per minute). Cloned to `data/statsbomb/`.

**Rate limit rule:** Cache all API responses locally in `.tmp/YYYY-MM-DD/`. Never re-fetch same endpoint+params twice per day. Sleep 7s between api-sports.io calls.

---

## Prediction Model (v2.0.0 — WC2026-only Poisson + shrinkage)
Computed entirely inside `tools/predict_wc2026.py` from finished WC2026 matches in the `matches`
table. **No historical martj42 data, no Dixon-Coles MLE, no `dc_params.json`.**

- For each team over its finished WC matches: `played`, `gf`, `ga`.
- Tournament mean `mu = total_goals / (2 * n_finished_matches)` (fallback `DEFAULT_MU=1.35`).
- Shrinkage with `SHRINK_K=3` pseudo-matches at the mean (stable with 1-2 games played):
  `att = (gf + k*mu)/(played + k)`, `def = (ga + k*mu)/(played + k)`; strengths `A=att/mu`, `D=def/mu`.
- Match h vs a (neutral; hosts USA/CAN/MEX get `HOST_BUMP=1.10`):
  `lam = mu*A_h*D_a`, `mu_a = mu*A_a*D_h` → independent bivariate Poisson grid → 1X2 + argmax scoreline.
- Teams with 0 WC matches default to strength 1.0 (league-average).
- Writes 3 markets per match: `winner`, `probabilities`, `scoreline`. (No corners/cards.)
- `model_meta` key `wc_model` = `{model, n_wc_matches, mu_league, k, teams_rated, updated}`;
  `/api/model_info` exposes it for the "Modelo Predictivo" banner.
- `tools/collect_training_data.py` + `tools/train_model.py` are **deprecated** (left in repo, not run).

Confidence thresholds (display tier only — no staking): HIGH ≥ 65, MED 50–64, LOW < 50.

Runs daily via cron in `scripts/run_daily.sh` (fetch_fixtures → fetch_standings → fetch_player_stats
→ fetch_team_stats → fetch_intl_stats → predict_wc2026 → fetch_reddit_sentiment → sync_to_supabase).

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

-- v2.0.0: `bets` table + log_bet.py REMOVED (no money tracking).

CREATE TABLE standings (             -- v2.0.0, populated by fetch_standings.py
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT, team_id INTEGER, group_label TEXT,
  position INTEGER, played INTEGER, won INTEGER, draw INTEGER, lost INTEGER,
  gf INTEGER, ga INTEGER, gd INTEGER, points INTEGER, updated_at TEXT,
  UNIQUE(team_id, group_label)
);

CREATE TABLE team_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT,
  stat_date TEXT,
  avg_corners_for REAL,            -- still fetched but unused by predictions in v2.0.0
  avg_corners_against REAL,
  avg_cards REAL,
  goals_1h REAL,
  goals_2h REAL,
  form_last10 TEXT,               -- JSON array of W/D/L
  fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## Workflow Sequence (daily — v2.0.0)
1. `fetch_fixtures.py` — get next 48h WC matches + live scores/status
2. `fetch_standings.py` — WC group table (position, points, GD) → `standings`
3. `fetch_player_stats.py` — WC scorers (goals/assists) → top scorer per team
4. `fetch_team_stats.py` — team form/goals (api-sports.io)
5. `fetch_intl_stats.py` — extended team stats (FIFA rank, confederation, form) from martj42
6. `predict_wc2026.py` — WC2026-only Poisson+shrinkage; writes `winner`/`probabilities`/`scoreline`
   + `model_meta.wc_model`. Only scores matches with `home_team`/`away_team` NOT NULL. Knockout
   fixtures stay NULL until the bracket resolves (EXPECTED) and predict automatically once named.
7. `fetch_reddit_sentiment.py` — GNews + Haiku crowd sentiment → `match_sentiment`
8. `sync_to_supabase.py` — push read-only snapshot to Supabase (Vercel dashboard)

All steps run via `bash scripts/run_daily.sh` (cron: `0 11 * * *` = 6 AM PE time).
`generate_report.py` / `telegram_send.py` remain available for the D-1 Telegram push.

---

## API Cost Rules
Cache everything. Never call same endpoint twice same day. Log all API calls with timestamp.
