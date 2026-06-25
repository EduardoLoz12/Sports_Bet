-- Sports Agent dashboard — read-only Supabase replica
-- Mirrors the subset of sports_agent.db (SQLite) that dashboard/app.py reads.
-- Run once in Supabase SQL Editor. tools/sync_to_supabase.py keeps it in sync
-- (full DELETE + re-INSERT on every daily run from Hetzner).
--
-- All date/time columns are TEXT (ISO strings), matching the SQLite source,
-- since app.py does string slicing/compares on them (e.g. kickoff_utc <= ?).

CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    match_id TEXT UNIQUE,
    home_team TEXT,
    away_team TEXT,
    home_team_id INTEGER,
    away_team_id INTEGER,
    kickoff_utc TEXT,
    group_stage TEXT,
    stage TEXT,
    status TEXT DEFAULT 'SCHEDULED',
    home_score INTEGER,
    away_score INTEGER
);
-- Migrate existing Supabase instance (safe to re-run):
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_score INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_score INTEGER;

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    match_id TEXT,
    market TEXT,
    pick TEXT,
    confidence INTEGER,
    odds REAL,
    stake_tier TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS bets (
    id SERIAL PRIMARY KEY,
    match_id TEXT,
    market TEXT,
    pick TEXT,
    odds REAL,
    stake_soles REAL,
    result TEXT DEFAULT 'pending',
    profit_soles REAL,
    placed_at TEXT,
    settled_at TEXT
);

CREATE TABLE IF NOT EXISTS team_stats (
    id SERIAL PRIMARY KEY,
    team_id INTEGER,
    team TEXT,
    stat_date TEXT,
    avg_corners_for REAL DEFAULT 0,
    avg_corners_against REAL DEFAULT 0,
    avg_cards REAL DEFAULT 0,
    goals_1h REAL DEFAULT 0,
    goals_2h REAL DEFAULT 0,
    goals_scored_avg REAL DEFAULT 0,
    goals_conceded_avg REAL DEFAULT 0,
    form_last10 TEXT DEFAULT '[]',
    league_id INTEGER,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS team_extended_stats (
    id SERIAL PRIMARY KEY,
    team TEXT UNIQUE,
    fifa_rank INTEGER,
    elo_approx INTEGER,
    wc_titles INTEGER,
    continental_titles INTEGER,
    confederation TEXT,
    total_matches INTEGER,
    win_pct REAL, draw_pct REAL, loss_pct REAL,
    gf_avg REAL, ga_avg REAL, goal_diff_avg REAL,
    comp_gf_avg REAL, comp_ga_avg REAL, comp_win_pct REAL,
    clean_sheet_pct REAL, big_win_pct REAL, big_loss_pct REAL,
    form5 TEXT, form10 TEXT,
    pts_pct_5 REAL, pts_pct_10 REAL, pts_pct_20 REAL,
    last_match TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS player_stats (
    id SERIAL PRIMARY KEY,
    player_id INTEGER,
    player_name TEXT,
    team_id INTEGER,
    team TEXT,
    stat_date TEXT,
    goals_total INTEGER DEFAULT 0,
    assists INTEGER DEFAULT 0,
    penalties INTEGER DEFAULT 0,
    goals_per90 REAL DEFAULT 0,
    minutes_played INTEGER DEFAULT 0,
    appearances INTEGER DEFAULT 0,
    cards_yellow INTEGER DEFAULT 0,
    cards_red INTEGER DEFAULT 0,
    fetched_at TEXT
);

-- Holds models/dc_params.json and models/eval_report.json contents,
-- so /api/model_info works without filesystem access on Vercel.
CREATE TABLE IF NOT EXISTS model_meta (
    key TEXT PRIMARY KEY,
    json TEXT
);

-- Reddit/GNews crowd sentiment per match (populated by tools/fetch_reddit_sentiment.py).
CREATE TABLE IF NOT EXISTS match_sentiment (
    id SERIAL PRIMARY KEY,
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
);

-- WC2026 group standings (populated by tools/fetch_standings.py). v2.0.0.
CREATE TABLE IF NOT EXISTS standings (
    id SERIAL PRIMARY KEY,
    team TEXT,
    team_id INTEGER,
    group_label TEXT,
    position INTEGER,
    played INTEGER DEFAULT 0,
    won INTEGER DEFAULT 0,
    draw INTEGER DEFAULT 0,
    lost INTEGER DEFAULT 0,
    gf INTEGER DEFAULT 0,
    ga INTEGER DEFAULT 0,
    gd INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0,
    updated_at TEXT,
    UNIQUE(team_id, group_label)
);
