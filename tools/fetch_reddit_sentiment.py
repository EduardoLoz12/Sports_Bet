"""
Fetch news sentiment for upcoming WC matches using GNews API + Claude Haiku.

Searches GNews for recent articles about each match in the next 3 days.
Analyzes with Haiku to get crowd win-probability split + key themes.
Cached per match_id for 12h — re-fetched on each daily cron run before kickoff.

Requires in .env: GNEWS_API_KEY, ANTHROPIC_API_KEY
Free tier: 100 req/day at gnews.io — sign up with email, instant.
Install:   pip install anthropic requests
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"

GNEWS_API_KEY     = os.getenv("GNEWS_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

MAX_ARTICLES   = 10
CACHE_HOURS    = 12
LOOKAHEAD_DAYS = 1  # GNews free = 10 req/day; only fetch tomorrow's matches


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS match_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            home_win_pct INTEGER,
            draw_pct INTEGER,
            away_win_pct INTEGER,
            summary TEXT,
            top_themes TEXT,
            post_count INTEGER,
            fetched_at TEXT
        )
    """)
    conn.commit()


def get_upcoming_matches(conn):
    cutoff = (
        datetime.now(timezone.utc) + timedelta(days=LOOKAHEAD_DAYS)
    ).date().isoformat() + "T23:59:59"
    return conn.execute("""
        SELECT match_id, home_team, away_team, kickoff_utc
        FROM matches
        WHERE status IN ('SCHEDULED','TIMED')
          AND home_team IS NOT NULL AND away_team IS NOT NULL
          AND kickoff_utc <= ?
        ORDER BY kickoff_utc
    """, (cutoff,)).fetchall()


def already_fresh(conn, match_id):
    row = conn.execute(
        "SELECT fetched_at FROM match_sentiment WHERE match_id=?", (match_id,)
    ).fetchone()
    if not row:
        return False
    try:
        fetched = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - fetched).total_seconds() < CACHE_HOURS * 3600
    except Exception:
        return False


def _clean(name):
    """Remove chars that break GNews query strings."""
    return name.replace("-", " ").replace("'", "")


def fetch_articles(home, away):
    """Search GNews for recent articles about this match."""
    query = f"{_clean(home)} {_clean(away)} World Cup 2026"
    url = "https://gnews.io/api/v4/search"
    try:
        r = requests.get(url, params={
            "q": query,
            "lang": "en",
            "max": MAX_ARTICLES,
            "sortby": "relevance",
            "token": GNEWS_API_KEY,
        }, timeout=10)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        texts = []
        for a in articles:
            title = a.get("title", "")
            desc  = a.get("description", "")
            if title:
                texts.append(f"{title}. {desc}".strip())
        return texts
    except Exception as e:
        print(f"    GNews error: {e}")
        return []


def analyze_with_haiku(ac, home, away, articles):
    """Single Haiku call: extract sentiment as structured JSON."""
    sample = "\n---\n".join(articles[:MAX_ARTICLES])

    resp = ac.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        messages=[{
            "role": "user",
            "content": (
                f"Analyze these news headlines/descriptions about the upcoming match "
                f"{home} vs {away}.\n\n"
                f"Articles:\n{sample}\n\n"
                "Based on the tone and content, estimate the public/media sentiment.\n"
                "Respond with ONLY valid JSON (no markdown, no explanation):\n"
                "{\n"
                f'  "home_win_pct": <integer 0-100, media confidence {home} wins>,\n'
                '  "draw_pct": <integer 0-100>,\n'
                f'  "away_win_pct": <integer 0-100, media confidence {away} wins>,\n'
                '  "summary": "<1-2 sentence media sentiment summary>",\n'
                '  "top_themes": ["<theme1>", "<theme2>", "<theme3>"]\n'
                "}\n"
                "The three _pct values must sum to exactly 100."
            ),
        }],
    )

    raw = resp.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = json.loads(raw)

    # Normalize to sum=100
    total = data["home_win_pct"] + data["draw_pct"] + data["away_win_pct"]
    if total > 0 and total != 100:
        data["home_win_pct"] = round(data["home_win_pct"] * 100 / total)
        data["draw_pct"]     = round(data["draw_pct"]     * 100 / total)
        data["away_win_pct"] = 100 - data["home_win_pct"] - data["draw_pct"]

    return data


def main():
    if not GNEWS_API_KEY:
        print("GNEWS_API_KEY not set — skipping sentiment")
        return
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY not set — skipping sentiment")
        return

    import anthropic
    ac = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    matches = get_upcoming_matches(conn)
    print(f"Sentiment: {len(matches)} upcoming matches to check")

    for m in matches:
        mid, home, away = m["match_id"], m["home_team"], m["away_team"]

        if already_fresh(conn, mid):
            print(f"  {home} vs {away}: cached (<{CACHE_HOURS}h), skip")
            continue

        print(f"  {home} vs {away}: fetching GNews…")
        articles = fetch_articles(home, away)
        print(f"    {len(articles)} articles")

        if not articles:
            print(f"    no articles found — skip")
            continue

        try:
            result = analyze_with_haiku(ac, home, away, articles)
        except Exception as e:
            print(f"    Haiku error: {e} — skip")
            continue

        conn.execute("""
            INSERT INTO match_sentiment
                (match_id, home_team, away_team, home_win_pct, draw_pct, away_win_pct,
                 summary, top_themes, post_count, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(match_id) DO UPDATE SET
                home_win_pct=excluded.home_win_pct,
                draw_pct=excluded.draw_pct,
                away_win_pct=excluded.away_win_pct,
                summary=excluded.summary,
                top_themes=excluded.top_themes,
                post_count=excluded.post_count,
                fetched_at=excluded.fetched_at
        """, (
            mid, home, away,
            result["home_win_pct"], result["draw_pct"], result["away_win_pct"],
            result["summary"],
            json.dumps(result["top_themes"]),
            len(articles),
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()

        print(
            f"    {home} {result['home_win_pct']}% · "
            f"Draw {result['draw_pct']}% · "
            f"{away} {result['away_win_pct']}%"
        )
        time.sleep(2)

    conn.close()
    print("Sentiment fetch complete")


if __name__ == "__main__":
    main()
