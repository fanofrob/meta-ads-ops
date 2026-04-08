-- Creative Intelligence Schema
-- Secondary subsystem — isolated from Meta ads ops pipeline.
-- All tables prefixed ci_ to avoid collisions if ever sharing a DB.
-- SQLite-compatible SQL.
-- Vision tables added in v1.1 (see bottom of file).

-- ─────────────────────────────────────────────
-- CREATIVE INVENTORY
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creatives (
    id                  TEXT PRIMARY KEY,   -- Meta creative_id (or ad_id fallback)
    ad_id               TEXT NOT NULL,
    ad_name             TEXT,
    adset_id            TEXT,
    adset_name          TEXT,
    campaign_id         TEXT,
    campaign_name       TEXT,
    status              TEXT,               -- ACTIVE, PAUSED, ARCHIVED
    format              TEXT,               -- image, video, carousel, collection, dco
    -- Copy fields (populated by creative_fetcher or parsed from ad name)
    hook_text           TEXT,
    primary_text        TEXT,
    headline            TEXT,
    description         TEXT,
    cta                 TEXT,
    destination_url     TEXT,
    -- Enrichment
    product_id          TEXT REFERENCES products(id),
    -- Lifecycle
    first_seen_date     TEXT,
    last_seen_date      TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_creatives_ad_id       ON creatives(ad_id);
CREATE INDEX IF NOT EXISTS idx_creatives_campaign_id ON creatives(campaign_id);
CREATE INDEX IF NOT EXISTS idx_creatives_product_id  ON creatives(product_id);


-- ─────────────────────────────────────────────
-- PERFORMANCE SNAPSHOTS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creative_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    creative_id     TEXT NOT NULL REFERENCES creatives(id),
    ad_id           TEXT NOT NULL,
    date_range      TEXT NOT NULL,          -- "7d", "30d", "yesterday", "lifetime"
    snapshot_date   TEXT NOT NULL,          -- YYYY-MM-DD when snapshot was taken
    -- Core metrics
    spend           REAL,
    impressions     INTEGER,
    clicks          INTEGER,
    ctr             REAL,
    cpc             REAL,
    cpm             REAL,
    frequency       REAL,
    -- Conversion metrics (populated where available)
    purchases       INTEGER,
    revenue         REAL,
    roas            REAL,
    cpa             REAL,
    -- Video metrics (null for non-video)
    video_views     INTEGER,
    video_view_rate REAL,
    video_p25       REAL,
    video_p50       REAL,
    video_p75       REAL,
    video_p100      REAL,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(ad_id, date_range, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_perf_creative_id   ON creative_performance(creative_id);
CREATE INDEX IF NOT EXISTS idx_perf_snapshot_date ON creative_performance(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_perf_date_range    ON creative_performance(date_range);


-- ─────────────────────────────────────────────
-- CREATIVE TAGS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creative_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    creative_id TEXT NOT NULL REFERENCES creatives(id),
    tag_type    TEXT NOT NULL,  -- hook_type | angle | format | archetype |
                                --  emotional_trigger | offer_style | cta_type | visual_style
    tag_value   TEXT NOT NULL,
    confidence  REAL DEFAULT 1.0,   -- 0.0–1.0; lower for AI-assigned tags
    source      TEXT DEFAULT 'rule', -- 'rule' | 'ai' | 'manual'
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(creative_id, tag_type)
);

CREATE INDEX IF NOT EXISTS idx_tags_creative_id ON creative_tags(creative_id);
CREATE INDEX IF NOT EXISTS idx_tags_type_value  ON creative_tags(tag_type, tag_value);


-- ─────────────────────────────────────────────
-- CREATIVE INSIGHTS
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creative_insights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    creative_id  TEXT NOT NULL REFERENCES creatives(id),
    insight_type TEXT NOT NULL,  -- 'winner' | 'loser' | 'fatigued' | 'emerging' | 'outlier'
    insight_text TEXT,
    score        REAL,
    metadata     TEXT,           -- JSON blob for extra context
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_insights_creative_id  ON creative_insights(creative_id);
CREATE INDEX IF NOT EXISTS idx_insights_type         ON creative_insights(insight_type);


-- ─────────────────────────────────────────────
-- CREATIVE PATTERNS
-- Extracted from clusters of winning/losing creatives.
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creative_patterns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_name         TEXT NOT NULL,
    description          TEXT,
    -- Tag dimensions that define this pattern
    hook_type            TEXT,
    angle                TEXT,
    format               TEXT,
    archetype            TEXT,
    emotional_trigger    TEXT,
    -- Aggregate performance for creatives matching this pattern
    avg_ctr              REAL,
    avg_cpa              REAL,
    avg_roas             REAL,
    avg_spend            REAL,
    creative_count       INTEGER DEFAULT 0,
    winner_count         INTEGER DEFAULT 0,
    -- Example creative IDs (JSON array)
    example_creative_ids TEXT,
    created_at           TEXT DEFAULT (datetime('now')),
    updated_at           TEXT DEFAULT (datetime('now'))
);


-- ─────────────────────────────────────────────
-- PRODUCT KNOWLEDGE
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS products (
    id                  TEXT PRIMARY KEY,       -- slug or UUID
    name                TEXT NOT NULL,
    category            TEXT,
    price               REAL,
    description         TEXT,
    short_description   TEXT,
    positioning         TEXT,                   -- brand/market positioning statement
    target_persona      TEXT,                   -- primary persona summary
    tags                TEXT,                   -- comma-separated Shopify tags
    source              TEXT DEFAULT 'manual',  -- 'manual' | 'csv' | 'json' | 'shopify'
    shopify_product_id  TEXT,                   -- optional, for future Shopify sync
    active              INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_products_active ON products(active);


CREATE TABLE IF NOT EXISTS product_benefits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   TEXT NOT NULL REFERENCES products(id),
    benefit_type TEXT NOT NULL,     -- 'feature' | 'benefit' | 'pain_point' |
                                    --  'desired_outcome' | 'use_case' | 'persona'
    content      TEXT NOT NULL,
    priority     INTEGER DEFAULT 0, -- higher = more prominent in prompts
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_benefits_product_id ON product_benefits(product_id);
CREATE INDEX IF NOT EXISTS idx_benefits_type       ON product_benefits(benefit_type);


CREATE TABLE IF NOT EXISTS product_angles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          TEXT NOT NULL REFERENCES products(id),
    angle_name          TEXT NOT NULL,
    angle_description   TEXT,
    target_persona      TEXT,
    emotional_trigger   TEXT,
    proof_points        TEXT,           -- JSON array of strings
    performance_score   REAL,           -- filled in after testing
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_angles_product_id ON product_angles(product_id);


CREATE TABLE IF NOT EXISTS product_offers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          TEXT NOT NULL REFERENCES products(id),
    offer_type          TEXT NOT NULL,  -- 'discount' | 'bundle' | 'free_shipping' |
                                        --  'trial' | 'guarantee' | 'gift_with_purchase'
    offer_description   TEXT,
    discount_pct        REAL,
    bundle_products     TEXT,           -- JSON array of product IDs
    active              INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_offers_product_id ON product_offers(product_id);


CREATE TABLE IF NOT EXISTS product_kb_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT REFERENCES products(id),   -- NULL = account-level doc
    doc_type    TEXT NOT NULL,  -- 'brief' | 'faq' | 'description' | 'review' |
                                --  'ugc_example' | 'offer_doc' | 'landing_page'
    title       TEXT,
    content     TEXT NOT NULL,
    source_file TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_kb_product_id ON product_kb_documents(product_id);
CREATE INDEX IF NOT EXISTS idx_kb_doc_type   ON product_kb_documents(doc_type);


-- ─────────────────────────────────────────────
-- GENERATION
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS generation_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type             TEXT NOT NULL,      -- 'hooks' | 'scripts' | 'briefs' |
                                             --  'test_matrix' | 'cta_variants'
    model                TEXT,
    prompt_summary       TEXT,
    input_creative_ids   TEXT,               -- JSON array
    input_pattern_ids    TEXT,               -- JSON array
    product_id           TEXT REFERENCES products(id),
    output_count         INTEGER,
    dry_run              INTEGER DEFAULT 1,  -- 1 = dry run (default safe)
    created_at           TEXT DEFAULT (datetime('now'))
);


CREATE TABLE IF NOT EXISTS generated_hooks (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES generation_runs(id),
    hook_text               TEXT NOT NULL,
    hook_type               TEXT,           -- curiosity | pain_point | social_proof |
                                            --  authority | direct_offer | story | shock
    angle                   TEXT,
    based_on_creative_ids   TEXT,           -- JSON array
    based_on_pattern_id     INTEGER REFERENCES creative_patterns(id),
    product_id              TEXT REFERENCES products(id),
    score                   REAL,
    status                  TEXT DEFAULT 'draft',  -- 'draft' | 'approved' | 'rejected' | 'launched'
    notes                   TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hooks_run_id    ON generated_hooks(run_id);
CREATE INDEX IF NOT EXISTS idx_hooks_status    ON generated_hooks(status);
CREATE INDEX IF NOT EXISTS idx_hooks_hook_type ON generated_hooks(hook_type);


CREATE TABLE IF NOT EXISTS generated_scripts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES generation_runs(id),
    script_type             TEXT NOT NULL,  -- 'ugc_brief' | 'static_brief' |
                                            --  'script_concept' | 'cta_variant' |
                                            --  'headline' | 'primary_text' | 'test_matrix'
    title                   TEXT,
    content                 TEXT NOT NULL,
    hook_id                 INTEGER REFERENCES generated_hooks(id),
    based_on_creative_ids   TEXT,           -- JSON array
    product_id              TEXT REFERENCES products(id),
    score                   REAL,
    status                  TEXT DEFAULT 'draft',
    notes                   TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scripts_run_id      ON generated_scripts(run_id);
CREATE INDEX IF NOT EXISTS idx_scripts_script_type ON generated_scripts(script_type);
CREATE INDEX IF NOT EXISTS idx_scripts_status      ON generated_scripts(status);


-- ─────────────────────────────────────────────
-- SCORING
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creative_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- exactly one of these should be set (existing OR proposed creative)
    creative_id         TEXT REFERENCES creatives(id),
    generated_hook_id   INTEGER REFERENCES generated_hooks(id),
    generated_script_id INTEGER REFERENCES generated_scripts(id),
    score_type          TEXT NOT NULL,  -- 'performance' | 'structural' |
                                        --  'pattern_match' | 'overall'
    score               REAL NOT NULL,  -- 0–100
    rationale           TEXT,
    metadata            TEXT,           -- JSON
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scores_creative_id ON creative_scores(creative_id);


-- ─────────────────────────────────────────────
-- VISION / IMAGE ANALYSIS
-- Isolated from core tables. Optional — core pipeline never reads these.
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creative_visual_analysis_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_label           TEXT,               -- e.g. "top_20_7d_2026-04-07"
    provider            TEXT NOT NULL,      -- 'openai' | 'mock' | future providers
    model               TEXT NOT NULL,      -- e.g. 'gpt-4o'
    analysis_version    TEXT DEFAULT '1',
    creative_ids_input  TEXT,               -- JSON array of creative_ids queued
    processed_count     INTEGER DEFAULT 0,
    failed_count        INTEGER DEFAULT 0,
    dry_run             INTEGER DEFAULT 1,
    started_at          TEXT DEFAULT (datetime('now')),
    completed_at        TEXT
);


CREATE TABLE IF NOT EXISTS creative_visual_attributes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    creative_id             TEXT NOT NULL REFERENCES creatives(id),
    run_id                  INTEGER REFERENCES creative_visual_analysis_runs(id),
    asset_url               TEXT,           -- URL analyzed (image or thumbnail)
    asset_type              TEXT,           -- 'image' | 'thumbnail' | 'frame'
    analyzed_at             TEXT DEFAULT (datetime('now')),
    analysis_provider       TEXT,           -- 'openai' | 'mock'
    analysis_model          TEXT,
    analysis_version        TEXT DEFAULT '1',
    -- Structured visual attributes
    visual_format           TEXT,   -- ugc | studio | meme | product_only | testimonial |
                                    --  lifestyle | before_after | screenshot_style | unknown
    subject_type            TEXT,   -- person | product | person_and_product | text_only | mixed
    shot_type               TEXT,   -- close_up | medium | wide | macro | cropped
    composition_style       TEXT,   -- selfie | handheld | polished | static | collage | split_screen
    background_type         TEXT,   -- home | kitchen | bathroom | studio | plain | outdoor | office | mixed
    text_overlay_presence   INTEGER,        -- 0/1 boolean
    text_overlay_density    TEXT,           -- none | low | medium | high
    branding_visibility     TEXT,           -- none | low | medium | high
    product_visibility      TEXT,           -- none | low | medium | high
    face_presence           INTEGER,        -- 0/1 boolean
    face_count              INTEGER,
    emotion_or_expression   TEXT,           -- happy | surprised | neutral | confident | excited | unknown
    visual_energy           TEXT,           -- low | medium | high
    color_feel              TEXT,           -- bright | muted | dark | neutral | mixed
    scroll_stopping_elements TEXT,          -- JSON array of strings
    visual_hook_description TEXT,
    visual_summary          TEXT,
    -- Raw LLM response for reprocessing
    raw_response_json       TEXT,
    -- Error tracking
    error                   TEXT,           -- set if analysis failed
    UNIQUE(creative_id, analysis_version)
);

CREATE INDEX IF NOT EXISTS idx_visual_creative_id ON creative_visual_attributes(creative_id);
CREATE INDEX IF NOT EXISTS idx_visual_format       ON creative_visual_attributes(visual_format);
CREATE INDEX IF NOT EXISTS idx_visual_face         ON creative_visual_attributes(face_presence);


-- ─────────────────────────────────────────────
-- CREATIVE TEST LAB
-- Human feedback on generated hooks from the /test UI.
-- Calibration source for pattern gap detection.
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS creative_tests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,           -- UUID grouping hooks from one generation run
    product_id      TEXT,           -- product selected in form (may be NULL)
    goal            TEXT,           -- 'conversions' | 'traffic' | 'awareness'
    audience        TEXT,           -- free-text audience description
    angle_override  TEXT,           -- angle requested in form (NULL = auto)
    hook_text       TEXT NOT NULL,
    predicted_score REAL,           -- 0–100 computed at generation time
    human_rating    INTEGER,        -- 1–5 star rating (NULL until rated)
    angle           TEXT,           -- angle tag from generating pattern
    emotion         TEXT,           -- emotional_trigger from generating pattern
    format          TEXT DEFAULT 'hook',
    pattern_name    TEXT,           -- pattern this hook came from
    is_duplicate    INTEGER DEFAULT 0,  -- 1 if operator flagged as duplicate
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tests_session_id    ON creative_tests(session_id);
CREATE INDEX IF NOT EXISTS idx_tests_product_id    ON creative_tests(product_id);
CREATE INDEX IF NOT EXISTS idx_tests_human_rating  ON creative_tests(human_rating);
CREATE INDEX IF NOT EXISTS idx_tests_created_at    ON creative_tests(created_at);


-- ─────────────────────────────────────────────
-- CREATIVE COPILOT  (v1.2)
-- Iterative creative workspace at /copilot.
-- Additive-only — no changes to existing tables.
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS copilot_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT UNIQUE NOT NULL,   -- UUID
    product_id  TEXT,                   -- Shopify/DB product ID (NULL = no context)
    goal        TEXT DEFAULT 'conversions',
    audience    TEXT DEFAULT '',
    tone        TEXT DEFAULT 'authentic',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_csess_session_id ON copilot_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_csess_created_at ON copilot_sessions(created_at);

-- One row per concept generated or entered in the copilot workspace.
-- Chains via parent_id to form a tree of refinements within a session.
CREATE TABLE IF NOT EXISTS copilot_iterations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,      -- FK → copilot_sessions.session_id
    parent_id       INTEGER,            -- FK → copilot_iterations.id (NULL = root)
    product_id      TEXT,               -- product context used at generation time
    action_type     TEXT NOT NULL,      -- 'initial_generate' | 'rewrite' | 'variants' |
                                        -- 'premium' | 'direct_response' | 'curiosity' |
                                        -- 'mainstream' | 'adapt_audience' | 'ugc_concepts' |
                                        -- 'static_concepts' | 'script' | 'creator_brief'
    concept_text    TEXT NOT NULL,      -- hook, script, brief, etc. — the raw output
    predicted_score REAL,               -- structural+pattern composite (NULL for rich formats)
    is_favorite     INTEGER DEFAULT 0,  -- 1 = starred by operator
    metadata        TEXT DEFAULT '{}',  -- JSON: angle, pattern_id, extra structured fields
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_citer_session_id ON copilot_iterations(session_id);
CREATE INDEX IF NOT EXISTS idx_citer_parent_id  ON copilot_iterations(parent_id);
CREATE INDEX IF NOT EXISTS idx_citer_is_fav     ON copilot_iterations(is_favorite);
CREATE INDEX IF NOT EXISTS idx_citer_created_at ON copilot_iterations(created_at);

-- Pattern-alignment explanations attached to iterations.
CREATE TABLE IF NOT EXISTS copilot_explanations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_id    INTEGER NOT NULL,   -- FK → copilot_iterations.id
    explanation     TEXT NOT NULL,      -- markdown text from LLM
    score_breakdown TEXT DEFAULT '{}',  -- JSON: {structural, pattern_match, overall}
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cexp_iteration_id ON copilot_explanations(iteration_id);


-- ─────────────────────────────────────────────
-- PRODUCTION HANDOFF  (v1.3)
-- Structured production outputs from approved copilot concepts.
-- Bridge between creative ideation and design/creator execution.
-- Additive-only — no changes to existing tables.
-- ─────────────────────────────────────────────

-- Single-concept production outputs: static brief, UGC brief, script package.
-- Each row is one LLM-elaborated document derived from a copilot concept.
CREATE TABLE IF NOT EXISTS production_outputs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    output_type          TEXT NOT NULL,      -- static_brief | ugc_brief | script_package
    source_iteration_id  INTEGER,            -- FK → copilot_iterations.id (NULL = standalone)
    session_id           TEXT,               -- FK → copilot_sessions.session_id
    product_id           TEXT,               -- product context used at generation time
    concept_text         TEXT,               -- the input concept / hook verbatim
    output_json          TEXT DEFAULT '{}',  -- full structured output as JSON (machine-consumable)
    output_md            TEXT DEFAULT '',    -- pre-rendered Markdown for human handoff
    is_approved          INTEGER DEFAULT 0,  -- 1 = approved for production
    is_favorite          INTEGER DEFAULT 0,  -- 1 = starred by operator
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prod_outputs_session  ON production_outputs(session_id);
CREATE INDEX IF NOT EXISTS idx_prod_outputs_type     ON production_outputs(output_type);
CREATE INDEX IF NOT EXISTS idx_prod_outputs_approved ON production_outputs(is_approved);

-- Multi-concept test bundles: one core concept + up to 5 variants, with scoring context.
-- Assembled from existing session iterations — no LLM call required.
-- Designed to map directly to a creative test in Meta Ads (one per adset).
CREATE TABLE IF NOT EXISTS production_packages (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id             TEXT,
    product_id             TEXT,
    core_concept           TEXT NOT NULL,         -- the primary approved hook
    core_iteration_id      INTEGER,               -- FK → copilot_iterations.id (lineage)
    variants_json          TEXT DEFAULT '[]',     -- [{concept_text, action_type, predicted_score, iteration_id}]
    audience               TEXT DEFAULT '',
    goal                   TEXT DEFAULT 'conversions',
    pattern_alignment_json TEXT DEFAULT '{}',     -- top matching pattern {pattern_name, hook_type, angle, winner_count}
    score_summary_json     TEXT DEFAULT '{}',     -- {avg, min, max, count}
    output_md              TEXT DEFAULT '',
    is_approved            INTEGER DEFAULT 0,
    is_favorite            INTEGER DEFAULT 0,
    created_at             TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prod_pkg_session  ON production_packages(session_id);
CREATE INDEX IF NOT EXISTS idx_prod_pkg_approved ON production_packages(is_approved);

-- ─────────────────────────────────────────────
-- STATIC RENDERING LAYER  (v1.4)
-- Converts StaticAdBrief production outputs into structured render specs
-- and optional image-generation variants for internal review.
-- ─────────────────────────────────────────────

-- One row per render job (5 variant specs stored as a JSON array).
-- source_production_output_id → production_outputs.id (output_type='static_brief')
CREATE TABLE IF NOT EXISTS render_outputs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_production_output_id INTEGER NOT NULL,
    render_type                 TEXT NOT NULL DEFAULT 'static_spec',
    render_spec_json            TEXT NOT NULL DEFAULT '[]',
    -- status: 'spec_only' | 'images_generated' | 'images_failed'
    status                      TEXT NOT NULL DEFAULT 'spec_only',
    provider_name               TEXT,
    is_approved                 INTEGER DEFAULT 0,
    is_favorite                 INTEGER DEFAULT 0,
    created_at                  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_render_outputs_source
    ON render_outputs(source_production_output_id);
CREATE INDEX IF NOT EXISTS idx_render_outputs_status
    ON render_outputs(status);

-- One row per generated image asset (or mock placeholder path).
-- review_status: 'pending' | 'preferred' | 'rejected' | 'approved'
CREATE TABLE IF NOT EXISTS render_assets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    render_output_id    INTEGER NOT NULL,
    asset_type          TEXT NOT NULL DEFAULT 'image_variant',
    variant_label       TEXT,            -- minimal|premium|direct_response|reveal|product_hero
    asset_path_or_url   TEXT,            -- local file path or remote URL (no binary blobs)
    metadata_json       TEXT DEFAULT '{}',
    review_status       TEXT DEFAULT 'pending',
    is_favorite         INTEGER DEFAULT 0,
    is_ready_to_test    INTEGER DEFAULT 0,
    review_notes        TEXT DEFAULT '',
    reviewed_at         TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_render_assets_output
    ON render_assets(render_output_id);
