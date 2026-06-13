# WF3 — Report Generation & Telegram Push

**Trigger:** 8:00 PM Peru time (01:00 UTC) day before each match.

## Steps
1. `generate_report.py` — read predictions from DB for tomorrow's matches. Build formatted Telegram message per match.
2. `telegram_send.py` — push one message per match. Wait 2s between messages.

## Message format
```
🏆 [HOME] vs [AWAY] | [DATE] | [STAGE]
📊 Win: [HOME]% | Draw%| [AWAY]%  → [PICK] @ [ODDS] — [TIER]
⚽ Scorer: [PLAYER] ([TEAM]) [CONF]%  → [TIER]
⏱ 1H scorer: [PLAYER] [CONF]%  → [TIER]
📐 Corners: Over/Under [LINE]  → [TIER]
🟨 Cards: Over/Under [LINE]  → [TIER]
💰 Bets:
  • [PICK] @ [ODDS] → S/.[STAKE] ([TIER])
  • [PICK] @ [ODDS] → S/.[STAKE] ([TIER])
```

## Rules
- Never send more than one report per match per day.
- Skip markets with confidence < 40 entirely (don't confuse with LOW tier bets).
- If no HIGH confidence bets: still send report, note "no HIGH tier today."
