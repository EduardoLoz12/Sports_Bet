# WF2 — Match Analysis & Prediction

**Trigger:** After WF1 completes. Runs for each match with kickoff in next 24h.

## Steps
1. `scoring_model.py` — load team + player stats from DB. Run weighted model per market:
   - **Winner:** H2H record (30%), form last 10 (25%), FIFA ranking diff (20%), goals scored/conceded avg (25%)
   - **Corners:** avg corners for (40%) + avg corners against (40%) + possession style (20%)
   - **Cards:** team avg cards (35%) + opponent avg cards (35%) + referee avg (30%)
   - **Scorer:** goals/90 (50%) + minutes played (20%) + penalty taker (20%) + form (10%)
   - **1H scorer:** team 1H goal rate (40%) + player 1H goal rate (40%) + opposition 1H conceded (20%)
2. Output confidence score 0–100 per market. Stake tier: HIGH≥70 / MED 50–69 / LOW<50.
3. Write all predictions to `predictions` table.

## Model tuning
Weights are editable constants at top of `scoring_model.py`. Adjust after each WC match based on tracker results.
