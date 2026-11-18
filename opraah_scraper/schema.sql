-- Opraah creator knowledge base. SQLite is the source of truth; CSVs and the wiki are
-- generated from it. Observations are append-only and always carry source + time.
-- NULL means "not observed / not available", never zero.

CREATE TABLE IF NOT EXISTS roster_creator (
    vendor_code      TEXT PRIMARY KEY,
    name             TEXT,
    tier             TEXT,            -- exclusive | non_exclusive | ''
    team             TEXT,
    category         TEXT,
    location         TEXT,            -- roster-maintained city (creator's base)
    gender           TEXT,
    exec_poc         TEXT,
    roster_followers INTEGER,         -- as exported by the roster (not observed by us)
    roster_avg_views INTEGER,
    roster_er        REAL,
    raw_json         TEXT,
    source_file      TEXT,
    imported_at      TEXT NOT NULL
);

-- Many-to-many: a shared handle links to every vendor code that lists it. vendor_code ''
-- is a handle supplied without a roster record (plain username list).
CREATE TABLE IF NOT EXISTS roster_link (
    vendor_code TEXT NOT NULL,
    username    TEXT NOT NULL,
    source_file TEXT,
    linked_at   TEXT NOT NULL,
    PRIMARY KEY (vendor_code, username)
);

-- One Instagram account, whatever it is called today. ig_pk is Instagram's user id (web),
-- graph_id is the Graph API IG User id (Business Discovery). Either may be unknown.
CREATE TABLE IF NOT EXISTS ig_account (
    account_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ig_pk            TEXT UNIQUE,
    graph_id         TEXT UNIQUE,
    current_username TEXT NOT NULL,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL
);

-- Every handle ever used as input or observed for an account (aliases survive renames).
CREATE TABLE IF NOT EXISTS ig_handle (
    username        TEXT PRIMARY KEY,
    account_id      INTEGER REFERENCES ig_account(account_id),
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    last_full_at    TEXT,             -- last completed full (monthly) collection
    last_light_at   TEXT,             -- last completed light (weekly) collection
    last_status     TEXT,
    meta_available  INTEGER           -- 1 business discovery works, 0 not professional/not found, NULL unknown
);

CREATE TABLE IF NOT EXISTS profile_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES ig_account(account_id),
    username        TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    source          TEXT NOT NULL,    -- meta_bd | web_profile
    run_id          INTEGER,
    followers       INTEGER,
    following       INTEGER,
    media_count     INTEGER,
    full_name       TEXT,
    biography       TEXT,
    website         TEXT,
    bio_links_json  TEXT,
    is_verified     INTEGER,
    is_private      INTEGER,
    profile_pic_url TEXT,
    UNIQUE (account_id, source, run_id)
);

CREATE TABLE IF NOT EXISTS post (
    shortcode          TEXT PRIMARY KEY,
    media_pk           TEXT,
    graph_media_id     TEXT,
    owner_username     TEXT,
    owner_pk           TEXT,
    taken_at           TEXT,          -- ISO 8601 UTC (exact: meta_bd or post page)
    approx_date        TEXT,          -- day precision from Instagram's accessibility caption
    format             TEXT,          -- reel | image | carousel | video
    product_type       TEXT,
    caption            TEXT,
    hashtags_json      TEXT,
    mentions_json      TEXT,
    coauthors_json     TEXT,          -- NULL = not observed (meta_bd has no co-authors)
    usertags_json      TEXT,
    location_name      TEXT,          -- content geotag, NOT where the creator lives
    audio_json         TEXT,
    accessibility_caption TEXT,
    permalink          TEXT,
    metrics_hidden     INTEGER,
    detail_source      TEXT,          -- web_post when the post page was read
    detail_fetched_at  TEXT,
    first_seen         TEXT NOT NULL,
    last_seen          TEXT NOT NULL
);

-- How a post relates to an account. owner = account published it; coauthor = listed as
-- co-author on someone else's post; profile_association = shown on the account's grid
-- without returned co-author evidence.
CREATE TABLE IF NOT EXISTS account_post (
    account_id     INTEGER NOT NULL REFERENCES ig_account(account_id),
    shortcode      TEXT NOT NULL REFERENCES post(shortcode),
    relation       TEXT NOT NULL,
    surfaces_json  TEXT NOT NULL,     -- e.g. ["meta_bd","web_timeline","web_reels"]
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    PRIMARY KEY (account_id, shortcode)
);

CREATE TABLE IF NOT EXISTS post_metric (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shortcode   TEXT NOT NULL REFERENCES post(shortcode),
    observed_at TEXT NOT NULL,
    source      TEXT NOT NULL,        -- meta_bd | web_post | web_reels
    run_id      INTEGER,
    likes       INTEGER,
    comments    INTEGER,
    views       INTEGER,              -- meta_bd view_count (Reels; may include crossposted FB views)
    plays       INTEGER,              -- web play_count (Reels tab)
    UNIQUE (shortcode, source, run_id)
);

CREATE TABLE IF NOT EXISTS brand (
    handle     TEXT PRIMARY KEY,      -- Instagram handle of the brand account (lowercase)
    name       TEXT,
    category   TEXT,
    kind       TEXT NOT NULL DEFAULT 'brand',   -- brand | creator | media | unknown
    source     TEXT NOT NULL,         -- registry | observed_coauthor | observed_owner | observed_mention
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brand_evidence (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    shortcode     TEXT NOT NULL REFERENCES post(shortcode),
    account_id    INTEGER NOT NULL REFERENCES ig_account(account_id),
    brand_handle  TEXT NOT NULL,      -- handle, or "#tag" for hashtag-only evidence
    kind          TEXT NOT NULL,      -- collaboration_disclosed | coauthor | collab_post_owned_by_brand |
                                      -- product_integration | event_association | mention | hashtag | creator_collab
    evidence_text TEXT NOT NULL,
    source        TEXT NOT NULL,
    rule_id       TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'auto',  -- auto | needs_review | confirmed | rejected
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    UNIQUE (shortcode, account_id, brand_handle, kind)
);

CREATE TABLE IF NOT EXISTS run (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    mode         TEXT NOT NULL,       -- run | due
    input_key    TEXT NOT NULL,       -- fingerprint of the input handle list (resume key)
    input_files  TEXT,
    args_json    TEXT,
    totals_json  TEXT
);

CREATE TABLE IF NOT EXISTS run_account (
    run_id         INTEGER NOT NULL REFERENCES run(run_id),
    username       TEXT NOT NULL,
    depth          TEXT,              -- full | light | none
    status         TEXT NOT NULL,     -- ok | partial | private | not_found | rate_limited | challenge | error | skipped_not_due
    coverage_json  TEXT,
    requests_json  TEXT,
    posts_in_window INTEGER,
    elapsed_s      REAL,
    error          TEXT,
    started_at     TEXT,
    finished_at    TEXT,
    PRIMARY KEY (run_id, username)
);

CREATE TABLE IF NOT EXISTS request_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    run_id      INTEGER,
    username    TEXT,
    source      TEXT NOT NULL,        -- meta | web
    kind        TEXT NOT NULL,        -- business_discovery | profile_page | timeline_page | reels_page | post_page
    target      TEXT,
    outcome     TEXT NOT NULL,        -- ok | rate_limited | login_wall | not_found | error | ...
    http_status INTEGER,
    ms          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_post_metric_shortcode ON post_metric(shortcode, observed_at);
CREATE INDEX IF NOT EXISTS idx_profile_snapshot_account ON profile_snapshot(account_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_account_post_short ON account_post(shortcode);
CREATE INDEX IF NOT EXISTS idx_brand_evidence_brand ON brand_evidence(brand_handle);
CREATE INDEX IF NOT EXISTS idx_request_log_run ON request_log(run_id, source);
