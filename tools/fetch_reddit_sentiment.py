"""
Fetch Reddit sentiment for upcoming WC matches using PRAW + Claude Haiku.

Searches r/worldcup and r/soccer for posts about each match in the next 3 days.
Analyzes with Haiku to get crowd-weighted win probabilities + key themes.
Cached per match_id for 12h — re-fetched on each daily cron run before kickoff.

Requires: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, ANTHROPIC_API_KEY in .env
Install:   pip install praw anthropic
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "database" / "sports_agent.db"

REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "sports-agent/1.0")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY")

SUBREDDITS  = ["worldcup", "soccer"]
MAX_POSTS   = 5
MAX_COMMENTS = 50
CACHE_HOURS  = 12
LOOKAHEAD_DAYS = 3


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


def fetch_comments(reddit, home, away):
    """Search Reddit for posts about this match, return list of comment bodies."""
    query = f"{home} {away}"
    comments = []

    for sub in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub)
            for post in subreddit.search(query, sort="relevance", time_filter="week", limit=MAX_POSTS):
                post.comments.replace_more(limit=0)
                for c in post.comments.list()[:15]:
                    if hasattr(c, "body") and 20 < len(c.body) < 600:
                        comments.append(c.body)
                if len(comments) >= MAX_COMMENTS:
                    break
        except Exception as e:
            print(f"    Reddit r/{sub} error: {e}")
        if len(comments) >= MAX_COMMENTS:
            break

    return comments[:MAX_COMMENTS]


def analyze_with_haiku(ac, home, away, comments):
    """Single Haiku call: extract crowd sentiment as structured JSON."""
    sample = "\n---\n".join(comments[:30])

    resp = ac.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        messages=[{
            "role": "user",
            "content": (
                f"Analyze these Reddit comments about the upcoming match "
                f"{home} vs {away}.\n\n"
                f"Comments:\n{sample}\n\n"
                "Respond with ONLY valid JSON (no markdown, no explanation):\n"
                "{\n"
                f'  "home_win_pct": <integer 0-100, crowd confidence {home} wins>,\n'
                '  "draw_pct": <integer 0-100>,\n'
                f'  "away_win_pct": <integer 0-100, crowd confidence {away} wins>,\n'
                '  "summary": "<1-2 sentence crowd sentiment summary>",\n'
                '  "top_themes": ["<theme1>", "<theme2>", "<theme3>"]\n'
                "}\n"
                "The three _pct values must sum to exactly 100."
            ),
        }],
    )

    data = json.loads(resp.content[0].text.strip())

    # Normalize to sum=100
    total = data["home_win_pct"] + data["draw_pct"] + data["away_win_pct"]
    if total > 0 and total != 100:
        data["home_win_pct"] = round(data["home_win_pct"] * 100 / total)
        data["draw_pct"]     = round(data["draw_pct"]     * 100 / total)
        data["away_win_pct"] = 100 - data["home_win_pct"] - data["draw_pct"]

    return data


def main():
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        print("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set — skipping sentiment")
        return
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY not set — skipping sentiment")
        return

    import praw
    import anthropic

    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )
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

        print(f"  {home} vs {away}: fetching Reddit…")
        comments = fetch_comments(reddit, home, away)
        print(f"    {len(comments)} comments")

        if not comments:
            print(f"    no comments found — skip")
            continue

        try:
            result = analyze_with_haiku(ac, home, away, comments)
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
            len(comments),
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
