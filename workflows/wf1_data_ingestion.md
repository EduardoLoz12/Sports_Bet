# WF1 — Daily Data Ingestion

**Trigger:** Cron 6:00 AM Peru time (11:00 UTC) every day during World Cup.

## Steps
1. `fetch_fixtures.py` — get all WC matches in next 48h from API-Football. Write to `matches` table.
2. `fetch_team_stats.py` — for each team playing in next 48h, fetch last 10 matches: corners for/against, cards, 1H/2H goals, form. Update `team_stats`.
3. `fetch_player_stats.py` — for each squad, fetch top 5 goal threats: goals/90, minutes, set piece role.
4. Cache all raw API responses as JSON in `.tmp/YYYY-MM-DD/` — never re-fetch same day.

## Rate limit guard
API-Football free = 100 req/day. At WC peak (~8 matches/day), budget ~8 req fixtures + ~16 req team stats + ~16 req player stats = ~40 req. Leaves headroom.

## Failure handling
If API-Football fails, skip player stats (lower priority). Log error. Never block Telegram send.
