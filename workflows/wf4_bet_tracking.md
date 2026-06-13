# WF4 — Bet Tracking & P&L

**Trigger:** Manual — user runs after placing or settling a bet.

## Logging a bet (before match)
```
python tools/log_bet.py --action place \
  --match_id <id> --market <market> \
  --pick "<pick>" --odds 1.85 --stake 50
```
Writes to `bets` table with `result=pending`.

## Settling a bet (after match)
```
python tools/log_bet.py --action settle \
  --bet_id <id> --result win|loss|void
```
Calculates `profit_soles = (odds - 1) * stake` for win, `-stake` for loss, `0` for void.

## Dashboard
Flask app at `dashboard/app.py`. Shows:
- Running bankroll (BANKROLL_START + sum of profit_soles)
- ROI % per market type
- Win rate per market
- Best/worst bets table
- Calendar heatmap of bet results

Run locally: `python dashboard/app.py`
On Hetzner: served on PORT=8000, access via server IP.
