-- Idempotent baseline for new and existing Job Hunter PostgreSQL installations.
CREATE TABLE IF NOT EXISTS jobs (
            id SERIAL PRIMARY KEY, source TEXT NOT NULL, source_id TEXT,
            title TEXT NOT NULL, company TEXT, location TEXT, country TEXT,
            url TEXT NOT NULL UNIQUE, description TEXT, employment_type TEXT, salary TEXT,
            posted_at TEXT, match_score INTEGER NOT NULL DEFAULT 0,
            match_reasons TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'new',
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, raw_json TEXT
        , normalized_url TEXT);

CREATE TABLE IF NOT EXISTS hunt_runs (
            id SERIAL PRIMARY KEY, started_at TEXT, finished_at TEXT,
            sources_checked INTEGER, fetched INTEGER, new_jobs INTEGER, errors TEXT
        );

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS candidate_profile (
                id INTEGER PRIMARY KEY CHECK(id=1), full_name TEXT DEFAULT '', email TEXT DEFAULT '',
                phone TEXT DEFAULT '', linkedin_url TEXT DEFAULT '', headline TEXT DEFAULT '',
                experience_summary TEXT DEFAULT '', skills TEXT DEFAULT '[]', resume_text TEXT DEFAULT '',
                resume_filename TEXT DEFAULT '', updated_at TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS application_drafts (
                id SERIAL PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'prepared', tailored_headline TEXT NOT NULL,
                keyword_checklist TEXT NOT NULL, resume_guidance TEXT NOT NULL,
                cover_letter TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

CREATE TABLE IF NOT EXISTS application_events (
                id SERIAL PRIMARY KEY, draft_id INTEGER NOT NULL,
                event TEXT NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL,
                FOREIGN KEY(draft_id) REFERENCES application_drafts(id) ON DELETE CASCADE
            );

CREATE TABLE IF NOT EXISTS gmail_oauth_states (state_hash TEXT PRIMARY KEY, expires_at BIGINT NOT NULL);

CREATE TABLE IF NOT EXISTS gmail_tokens (
            id INTEGER PRIMARY KEY CHECK(id=1),
            access_token TEXT, refresh_token TEXT, token_expiry TEXT,
            email TEXT DEFAULT '', updated_at TEXT NOT NULL
        );

CREATE TABLE IF NOT EXISTS gmail_seen_messages (
            message_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL
        );

CREATE TABLE IF NOT EXISTS automation_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS application_attempts (
                    id SERIAL PRIMARY KEY,
                    job_key TEXT NOT NULL UNIQUE,
                    job_id INTEGER,
                    draft_id INTEGER,
                    portal TEXT NOT NULL DEFAULT '',
                    job_url TEXT NOT NULL DEFAULT '',
                    title TEXT DEFAULT '',
                    company TEXT DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'discovered',
                    reason TEXT DEFAULT '',
                    confirmation_evidence TEXT DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    locked_at TEXT,
                    locked_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

CREATE TABLE IF NOT EXISTS application_state_events (
                    id SERIAL PRIMARY KEY,
                    attempt_id INTEGER NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(attempt_id) REFERENCES application_attempts(id) ON DELETE CASCADE
                );

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS normalized_url TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(match_score DESC);

CREATE INDEX IF NOT EXISTS idx_jobs_country ON jobs(country);

CREATE INDEX IF NOT EXISTS idx_jobs_normalized_url ON jobs(normalized_url);


