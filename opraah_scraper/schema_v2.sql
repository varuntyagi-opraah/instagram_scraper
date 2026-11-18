-- v2 (18 Nov 2026): shares/reposts, metrics level, YouTube, roster YouTube links.
-- Column additions are applied by db.migrate() only when missing; tables are IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS roster_youtube (
    vendor_code TEXT NOT NULL,
    yt_ref      TEXT NOT NULL,         -- @handle | channel:UC... | user:name | c:name
    youtube_raw TEXT,
    channel_id  TEXT,                  -- filled once resolved
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (vendor_code, yt_ref)
);

CREATE TABLE IF NOT EXISTS yt_channel (
    channel_id   TEXT PRIMARY KEY,
    handle       TEXT,                 -- @handle (customUrl)
    title        TEXT,
    country      TEXT,
    published_at TEXT,
    uploads_playlist TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    last_full_at TEXT,
    last_status  TEXT
);

CREATE TABLE IF NOT EXISTS yt_channel_snapshot (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id    TEXT NOT NULL REFERENCES yt_channel(channel_id),
    observed_at   TEXT NOT NULL,
    run_id        INTEGER,
    subscribers   INTEGER,             -- NULL when hidden
    total_views   INTEGER,
    video_count   INTEGER,
    description   TEXT,
    keywords      TEXT,
    topics_json   TEXT,
    UNIQUE (channel_id, run_id)
);

CREATE TABLE IF NOT EXISTS yt_video (
    video_id        TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL REFERENCES yt_channel(channel_id),
    published_at    TEXT,
    title           TEXT,
    description     TEXT,
    duration_s      INTEGER,
    format          TEXT,              -- short_guess (<= 180 s) | video | live
    category_id     TEXT,
    tags_json       TEXT,
    default_language TEXT,
    made_for_kids   INTEGER,
    paid_placement  INTEGER,           -- creator ticked "includes paid promotion"
    topics_json     TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yt_video_metric (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL REFERENCES yt_video(video_id),
    observed_at TEXT NOT NULL,
    run_id      INTEGER,
    views       INTEGER,
    likes       INTEGER,               -- NULL when hidden
    comments    INTEGER,               -- NULL when disabled
    UNIQUE (video_id, run_id)
);

CREATE TABLE IF NOT EXISTS yt_brand_evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL REFERENCES yt_video(video_id),
    channel_id    TEXT NOT NULL,
    brand_handle  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    rule_id       TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'auto',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    UNIQUE (video_id, brand_handle, kind)
);

CREATE TABLE IF NOT EXISTS yt_request_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    run_id    INTEGER,
    channel   TEXT,
    endpoint  TEXT NOT NULL,
    units     INTEGER NOT NULL,
    outcome   TEXT NOT NULL,
    http_status INTEGER
);

CREATE INDEX IF NOT EXISTS idx_yt_video_channel ON yt_video(channel_id, published_at);
CREATE INDEX IF NOT EXISTS idx_yt_metric_video ON yt_video_metric(video_id, observed_at);
