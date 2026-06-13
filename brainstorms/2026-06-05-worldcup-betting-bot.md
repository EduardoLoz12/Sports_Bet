# World Cup Betting Bot: Brainstorm / Discovery Notes
Date: 2026-06-05 · Goal: Design a stats-driven World Cup betting assistant for the Peruvian market

## Summary / key decisions
- **Product:** Pre-match report bot for 2026 World Cup betting (Betano Peru)
- **Markets:** Match winner, anytime scorer, 1H/2H scorer, corners (total/team/1H), yellow cards (total/player/team)
- **Data:** API-Football (free, 100 req/day) + football-data.org (free) + StatsBomb open (historical). Daily refresh.
- **Model:** Weighted scoring model — combines stats into confidence score per market. No ML yet.
- **Staking:** Tiered by confidence (LOW/MED/HIGH → S/. amounts TBD)
- **Delivery:** Telegram push D-1 before each match + Flask web dashboard on Hetzner VPS
- **Tracker:** SQLite bet log → P&L dashboard showing ROI per market, win rate, bankroll
- **Stack:** Python, WAT architecture (same as GFV project), .env secrets, cron on Hetzner
- **Pending:** Hetzner SSH creds, Betano market list, bankroll + tier amounts

## Q&A log

### Q1 — Core output / bet markets
- Asked: What does the bot hand you before a match?
- Captured: Pre-match report per game. Bet markets to cover:
  - **Match winner** (1X2)
  - **Goalscorer** — who scores (anytime)
  - **First half / second half scorer**
  - **Corners** (total, team, first half)
  - **Yellow cards** (total, per player, per team)
  - Any additional markets that data supports
- Flags: none

### Q2 — Data freshness / update cadence
- Asked: How fresh does data need to be?
- Captured: **Daily updates for everything.** World Cup moves fast — no weekly batch.
  - Layer 1: Historical DB (stats, corners, cards, scorers) — updated daily
  - Layer 2: News/lineups/injuries/suspensions — updated daily, ideally D-1 before kickoff
  - No real-time in-play data needed (pre-match only)
- Flags: none

### Q3 — Tech stack / delivery
- Asked: How to run and receive reports?
- Captured: **Python + web dashboard + Telegram bot**
  - Dashboard (Flask/FastAPI) for deep analysis
  - Telegram bot pushes report D-1 before each match automatically
  - WhatsApp ruled out — no Meta Business account
- Flags: none

### Q4 — Peruvian betting platform
- Asked: Which platforms do you use?
- Captured: **Betano Peru** — only active account
  - Betano has no public API — requires web scraping or third-party odds aggregator
  - Will need to monitor Betano PE market availability per bet type (goalscorer, corners, cards)
- Flags: Confirm Betano PE offers all target markets (corners, cards, 1H scorer) -> user to verify in app

### Q5 — Football data API
- Asked: Paid or free API?
- Captured: **Free only.** Stack:
  - **API-Football** (RapidAPI free tier) — 100 req/day, covers WC corners, cards, lineups, player stats
  - **football-data.org** (free tier) — match data, standings, top scorers
  - **StatsBomb open data** — historical depth for model training (no live data)
  - Combine all three to stay within free limits
- Flags: Validate 100 req/day enough for WC peak (up to 8 games/day) -> dev

### Q6 — Prediction model
- Asked: How sophisticated should the model be?
- Captured: **Weighted scoring model (Option B)**
  - Combine multiple stats with tunable weights
  - Output confidence score per bet type
  - Transparent, explainable outputs
  - ML upgrade deferred to later phase
- Flags: none

### Q7 — Stake sizing
- Asked: How to decide bet amount?
- Captured: **Tiered staking (Option D)** — stake scales with model confidence score
  - Low confidence tier → small stake
  - High confidence tier → larger stake
  - Exact S/. amounts TBD once bankroll defined
- Flags: User to define bankroll size + tier thresholds (e.g. S/.20/S/.50/S/.100) -> user

### Q8 — Telegram report format
- Asked: What does the Telegram message look like?
- Captured: **Confirmed format** — one message per match, sent D-1 before kickoff:
  - Match header (teams, date, group)
  - Win probabilities (%)
  - Top goalscorer candidates + probability
  - Corners over/under + confidence level
  - Yellow cards over/under + confidence level
  - 1H scorer pick + confidence level
  - Recommended bets with odds, stake tier (HIGH/MED/LOW)
- Flags: none

### Q9 — Hosting / infrastructure
- Asked: Where does this run?
- Captured: **Hetzner VPS** — user already has a server running. Same stack as GFV project:
  - Python, WAT architecture (Workflows → Agents → Tools), .env for secrets
  - Reuse patterns from `C:\Users\eduar\Self-Finance\Self-Finance\FreeLance\Workflows - GFV`
  - Cron job on Hetzner triggers daily data fetch + D-1 Telegram push
  - Anthropic + OpenAI API keys already available in existing .env
- Flags: Hetzner IP/SSH not found in .env — user to confirm server connection details -> user

### Q10 — Bet tracker + dashboard
- Asked: Log actual bets and track P&L?
- Captured: **Yes — full bet tracker with web dashboard**
  - Log each bet: match, market type, odds, stake (S/.), result, profit/loss
  - Dashboard shows: running bankroll, ROI per market (corners vs cards vs winner vs scorer), win rate, best/worst performing markets
  - SQLite backend (same pattern as GFV)
  - Dashboard served via Flask/FastAPI on Hetzner
- Flags: none

## Open flags (pending input)
- Betano PE market availability: does it offer corners, yellow cards, 1H/2H scorer for WC? -> user to verify
- Validate free API rate limits are enough for daily WC match volume (~4-8 games/day at peak) -> dev
- Hetzner server IP + SSH credentials for deployment -> user
