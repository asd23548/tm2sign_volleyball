-- Volleyball analytics hierarchy schema

CREATE TABLE IF NOT EXISTS regions (
    region_id   TEXT PRIMARY KEY,
    region_name TEXT NOT NULL,
    state       TEXT
);

CREATE TABLE IF NOT EXISTS clubs (
    club_id    TEXT PRIMARY KEY,
    club_name  TEXT NOT NULL,
    region_id  TEXT REFERENCES regions(region_id)
);

CREATE TABLE IF NOT EXISTS teams (
    team_id        TEXT PRIMARY KEY,
    club_id        TEXT REFERENCES clubs(club_id),
    team_name      TEXT NOT NULL,
    age_group      TEXT,
    cohort_year    INTEGER,
    club_team_id   TEXT,
    alt_code       TEXT,
    age_num        INTEGER,
    tier_label     TEXT,
    gender_code    TEXT,
    program_id     TEXT,
    program_label  TEXT,
    age_team_key   TEXT
);

CREATE TABLE IF NOT EXISTS programs (
    program_id     TEXT PRIMARY KEY,
    program_label  TEXT NOT NULL,
    club_id        TEXT REFERENCES clubs(club_id),
    gender_code    TEXT,
    tier_label     TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    event_name  TEXT NOT NULL,
    start_date  TEXT,
    end_date    TEXT,
    location    TEXT,
    region_id   TEXT REFERENCES regions(region_id)
);

CREATE TABLE IF NOT EXISTS divisions (
    division_id   TEXT PRIMARY KEY,
    event_id      TEXT NOT NULL REFERENCES events(event_id),
    division_name TEXT NOT NULL,
    age_group     TEXT,
    gender        TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    match_id      TEXT PRIMARY KEY,
    division_id   TEXT NOT NULL REFERENCES divisions(division_id),
    match_date    TEXT,
    stage         TEXT,
    team_a_id     TEXT REFERENCES teams(team_id),
    team_b_id     TEXT REFERENCES teams(team_id),
    team_a_score  INTEGER,
    team_b_score  INTEGER,
    set_scores    TEXT,  -- JSON
    winner_id     TEXT REFERENCES teams(team_id),
    seed_a        INTEGER,
    seed_b        INTEGER
);

CREATE TABLE IF NOT EXISTS rankings (
    event_id       TEXT NOT NULL REFERENCES events(event_id),
    division_id    TEXT NOT NULL REFERENCES divisions(division_id),
    team_id        TEXT NOT NULL REFERENCES teams(team_id),
    initial_seed   INTEGER,
    final_rank     INTEGER,
    bracket_finish TEXT,
    PRIMARY KEY (event_id, division_id, team_id)
);

-- Placeholder for future player feeds (not available on public TM2 API today)
CREATE TABLE IF NOT EXISTS players (
    player_id    TEXT PRIMARY KEY,
    full_name    TEXT NOT NULL,
    gender       TEXT,
    grad_year    INTEGER
);

CREATE TABLE IF NOT EXISTS player_season_stints (
    player_id    TEXT NOT NULL REFERENCES players(player_id),
    event_id     TEXT NOT NULL REFERENCES events(event_id),
    team_id      TEXT REFERENCES teams(team_id),
    program_id   TEXT,
    age_group    TEXT,
    season_year  INTEGER,
    PRIMARY KEY (player_id, event_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_division ON matches(division_id);
CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(team_a_id, team_b_id);
CREATE INDEX IF NOT EXISTS idx_teams_club ON teams(club_id);
CREATE INDEX IF NOT EXISTS idx_clubs_region ON clubs(region_id);
CREATE INDEX IF NOT EXISTS idx_events_dates ON events(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_rankings_team ON rankings(team_id);
