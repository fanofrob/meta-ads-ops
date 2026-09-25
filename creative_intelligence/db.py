"""
SQLite connection and schema management for the creative intelligence subsystem.
Completely isolated from the Meta ads ops reporting pipeline.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# DB lives inside the project tree; never inside data/raw or outputs.
_DEFAULT_DB_PATH = Path(__file__).parent.parent / "creative_intelligence_data" / "creative_intel.db"


def get_db_path() -> Path:
    """Return DB path, respecting CI_DB_PATH env override."""
    import os
    override = os.getenv("CI_DB_PATH")
    return Path(override) if override else _DEFAULT_DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the SQLite database and return a connection.

    Enables WAL mode for better concurrent read performance and
    enforces foreign key constraints.
    """
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create all tables from schema.sql if they do not already exist."""
    schema_path = Path(__file__).parent / "models" / "schema.sql"
    sql = schema_path.read_text()
    conn = get_connection(db_path)
    with conn:
        conn.executescript(sql)
        _migrate(conn)
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column migrations for existing DBs (safe to re-run)."""
    migrations = [
        "ALTER TABLE products ADD COLUMN tags TEXT",
        # render_assets: decision/review columns added in v1.4.1
        "ALTER TABLE render_assets ADD COLUMN is_favorite INTEGER DEFAULT 0",
        "ALTER TABLE render_assets ADD COLUMN is_ready_to_test INTEGER DEFAULT 0",
        "ALTER TABLE render_assets ADD COLUMN review_notes TEXT DEFAULT ''",
        "ALTER TABLE render_assets ADD COLUMN reviewed_at TEXT",
        # render_outputs: store generation error for visibility without log access
        "ALTER TABLE render_outputs ADD COLUMN error_message TEXT",
        # ── video module (v1.5) ──────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS video_storyboards (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id                  TEXT,
            concept_text                TEXT NOT NULL,
            scenes_json                 TEXT,
            ugc_script_json             TEXT,
            total_duration_seconds      INTEGER DEFAULT 30,
            source_production_output_id INTEGER,
            source_output_type          TEXT DEFAULT 'concept',
            created_at                  TEXT DEFAULT (datetime('now'))
        )""",
        # Additive columns for storyboards created before lineage tracking
        "ALTER TABLE video_storyboards ADD COLUMN source_production_output_id INTEGER",
        "ALTER TABLE video_storyboards ADD COLUMN source_output_type TEXT DEFAULT 'concept'",
        """CREATE TABLE IF NOT EXISTS video_scenes (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            storyboard_id       INTEGER NOT NULL
                                    REFERENCES video_storyboards(id) ON DELETE CASCADE,
            scene_index         INTEGER NOT NULL,
            purpose             TEXT,
            visual_description  TEXT,
            text_overlay        TEXT DEFAULT '',
            duration_seconds    REAL  DEFAULT 5,
            camera_type         TEXT DEFAULT 'handheld',
            framing             TEXT DEFAULT 'medium',
            movement            TEXT DEFAULT 'none',
            product_focus       TEXT DEFAULT '',
            lighting_style      TEXT DEFAULT '',
            render_asset_id     INTEGER
        )""",
        "ALTER TABLE video_storyboards ADD COLUMN is_favorite INTEGER DEFAULT 0",
        "ALTER TABLE video_storyboards ADD COLUMN is_approved INTEGER DEFAULT 0",
        "ALTER TABLE video_storyboards ADD COLUMN review_notes TEXT DEFAULT ''",
        # ── video types + assembly (v1.6) ─────────────────────────────
        "ALTER TABLE video_storyboards ADD COLUMN video_type TEXT DEFAULT 'ugc'",
        "ALTER TABLE video_scenes ADD COLUMN video_type TEXT DEFAULT 'ugc'",
        """CREATE TABLE IF NOT EXISTS video_outputs (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            storyboard_id               INTEGER NOT NULL,
            video_type                  TEXT NOT NULL DEFAULT 'ugc',
            source_output_type          TEXT DEFAULT 'concept',
            source_production_output_id INTEGER,
            output_path                 TEXT,
            metadata_json               TEXT DEFAULT '{}',
            status                      TEXT NOT NULL DEFAULT 'pending',
            is_approved                 INTEGER DEFAULT 0,
            is_favorite                 INTEGER DEFAULT 0,
            is_ready_to_test            INTEGER DEFAULT 0,
            created_at                  TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE INDEX IF NOT EXISTS idx_video_outputs_storyboard
           ON video_outputs(storyboard_id)""",
        # ── scene-level clip generation (v1.7) ──────────────────────────
        """CREATE TABLE IF NOT EXISTS video_scene_clips (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            storyboard_id       INTEGER NOT NULL,
            scene_id            INTEGER NOT NULL,
            scene_index         INTEGER NOT NULL,
            provider            TEXT NOT NULL DEFAULT 'replicate',
            model               TEXT,
            prompt              TEXT,
            clip_local_path     TEXT,
            clip_url            TEXT,
            duration_seconds    REAL,
            aspect_ratio        TEXT DEFAULT '9:16',
            status              TEXT NOT NULL DEFAULT 'pending',
            error_message       TEXT,
            metadata_json       TEXT DEFAULT '{}',
            created_at          TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE INDEX IF NOT EXISTS idx_video_scene_clips_storyboard
           ON video_scene_clips(storyboard_id)""",
        """CREATE INDEX IF NOT EXISTS idx_video_scene_clips_scene
           ON video_scene_clips(scene_id)""",
        # Add prediction_id for async Replicate predictions (survives container restarts)
        "ALTER TABLE video_scene_clips ADD COLUMN prediction_id TEXT",
        # Add product image URL — used as first-frame reference for image-to-video clip generation
        "ALTER TABLE products ADD COLUMN image_url TEXT",
        # ── Hook D+C+PS framework (v1.6) ─────────────────────────────────
        # Store archetype label + D/C/PS breakdown + clarity note per generated hook
        "ALTER TABLE generated_hooks ADD COLUMN archetype TEXT",
        "ALTER TABLE generated_hooks ADD COLUMN dcp_d TEXT",
        "ALTER TABLE generated_hooks ADD COLUMN dcp_c TEXT",
        "ALTER TABLE generated_hooks ADD COLUMN dcp_ps TEXT",
        "ALTER TABLE generated_hooks ADD COLUMN dcp_clarity TEXT",
        # ── Creative scoring + learning system (v1.8) ────────────────────────
        "ALTER TABLE render_assets ADD COLUMN quality_score REAL",
        "ALTER TABLE render_assets ADD COLUMN score_breakdown_json TEXT",
        """CREATE TABLE IF NOT EXISTS asset_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id        INTEGER NOT NULL,
    asset_type      TEXT NOT NULL DEFAULT 'render',
    feedback_type   TEXT NOT NULL,
    rejection_tags  TEXT DEFAULT '[]',
    note            TEXT DEFAULT '',
    product_id      TEXT,
    variant_label   TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_asset_feedback_asset
   ON asset_feedback(asset_id)""",
        """CREATE INDEX IF NOT EXISTS idx_asset_feedback_product
   ON asset_feedback(product_id)""",
        """CREATE TABLE IF NOT EXISTS creative_learnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      TEXT,
    learning_type   TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'feedback',
    summary         TEXT NOT NULL,
    detail_json     TEXT DEFAULT '{}',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_creative_learnings_product
   ON creative_learnings(product_id)""",
        # GHF candle-label studio — isolated from the ads render pipeline.
        """CREATE TABLE IF NOT EXISTS label_renders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sku             TEXT NOT NULL,
    recipe          TEXT NOT NULL,
    code_s          TEXT NOT NULL,
    code_i          TEXT NOT NULL,
    code_e          TEXT NOT NULL,
    code_h          TEXT NOT NULL,
    code_p          TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    negative_prompt TEXT NOT NULL,
    image_path      TEXT,
    provider        TEXT,
    model           TEXT,
    status          TEXT NOT NULL DEFAULT 'spec_only',
    error_message   TEXT,
    is_favorite     INTEGER NOT NULL DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_label_renders_sku
   ON label_renders(sku, created_at DESC)""",
        # ── Ad Studio — reference-photo product ads (isolated) ──────────────
        """CREATE TABLE IF NOT EXISTS adstudio_products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    scent_notes   TEXT DEFAULT '',
    physical_desc TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now'))
)""",
        # add notes to existing DBs (no-op / silently ignored where it exists)
        "ALTER TABLE adstudio_products ADD COLUMN notes TEXT DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS adstudio_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL,
    path        TEXT NOT NULL,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_adstudio_photos_product
   ON adstudio_photos(product_id)""",
        """CREATE TABLE IF NOT EXISTS adstudio_hooks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL,
    hook_text   TEXT NOT NULL DEFAULT '',
    archetype   TEXT DEFAULT '',
    d           TEXT DEFAULT '',
    c           TEXT DEFAULT '',
    ps          TEXT DEFAULT '',
    headline    TEXT DEFAULT '',
    subhead     TEXT DEFAULT '',
    body        TEXT DEFAULT '',
    cta         TEXT DEFAULT '',
    source      TEXT DEFAULT 'manual',
    created_at  TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_adstudio_hooks_product
   ON adstudio_hooks(product_id)""",
        """CREATE TABLE IF NOT EXISTS adstudio_ads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL,
    hook_id       INTEGER,
    format_key    TEXT NOT NULL,
    aspect_ratio  TEXT NOT NULL DEFAULT '4:5',
    prompt        TEXT NOT NULL DEFAULT '',
    model         TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'queued',
    image_path    TEXT,
    error_message TEXT,
    is_favorite   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_adstudio_ads_product
   ON adstudio_ads(product_id, created_at DESC)""",
        # Ad Studio v2 — text-free image library (diversity seeds) + composites.
        """CREATE TABLE IF NOT EXISTS adstudio_images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL,
    archetype     TEXT DEFAULT '',
    title         TEXT DEFAULT '',
    concept       TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL DEFAULT '',
    aspect_ratio  TEXT NOT NULL DEFAULT '4:5',
    model         TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'queued',
    image_path    TEXT,
    error_message TEXT,
    is_favorite   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_adstudio_images_product
   ON adstudio_images(product_id, created_at DESC)""",
        """CREATE TABLE IF NOT EXISTS adstudio_composites (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL,
    image_id     INTEGER NOT NULL,
    hook_id      INTEGER,
    headline     TEXT DEFAULT '',
    subhead      TEXT DEFAULT '',
    cta          TEXT DEFAULT '',
    layout_json  TEXT DEFAULT '{}',
    output_path  TEXT,
    is_favorite  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_adstudio_composites_product
   ON adstudio_composites(product_id, created_at DESC)""",
        # after the CREATEs above, so fresh DBs get the column too
        "ALTER TABLE adstudio_images ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE adstudio_composites ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        # Core photos (core1-3) + box-size infographics (PDP / Build-a-Box).
        "ALTER TABLE adstudio_products ADD COLUMN varieties TEXT DEFAULT ''",
        "ALTER TABLE adstudio_products ADD COLUMN shopify_url TEXT DEFAULT ''",
        "ALTER TABLE adstudio_products ADD COLUMN info_json TEXT DEFAULT '{}'",
        """CREATE TABLE IF NOT EXISTS adstudio_core (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    variant       INTEGER NOT NULL DEFAULT 0,
    prompt        TEXT NOT NULL DEFAULT '',
    aspect_ratio  TEXT NOT NULL DEFAULT '1:1',
    status        TEXT NOT NULL DEFAULT 'queued',
    image_path    TEXT,
    error_message TEXT,
    is_favorite   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
)""",
        """CREATE INDEX IF NOT EXISTS idx_adstudio_core_product
   ON adstudio_core(product_id, kind, created_at DESC)""",
        # photo role: core1|core2|core3|infographic|other ('' = untagged)
        "ALTER TABLE adstudio_photos ADD COLUMN role TEXT DEFAULT ''",
        # Claude's per-shot brief + operator fix note, folded into the prompt at run time
        "ALTER TABLE adstudio_core ADD COLUMN brief TEXT DEFAULT ''",
        "ALTER TABLE adstudio_core ADD COLUMN fix TEXT DEFAULT ''",
        # product type drives the workflow: fruit | candle | other
        "ALTER TABLE adstudio_products ADD COLUMN product_type TEXT DEFAULT ''",
        """UPDATE adstudio_products SET product_type =
   CASE WHEN lower(name) LIKE '%candle%' THEN 'candle' ELSE 'fruit' END
   WHERE product_type IS NULL OR product_type = ''""",
        # which winning pattern (creative_patterns.id) a hook was written from
        "ALTER TABLE adstudio_hooks ADD COLUMN pattern_id INTEGER",
        # Meta → patterns refresh runs (read-only pull; see ingest/meta_sync.py)
        """CREATE TABLE IF NOT EXISTS meta_sync_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    step         TEXT DEFAULT '',
    message      TEXT DEFAULT '',
    date_range   TEXT DEFAULT '30d',
    n_ads        INTEGER,
    spend        REAL,
    n_patterns   INTEGER
)""",
    ]
    for stmt in migrations:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # Column/table already exists


def db_exists(db_path: Path | None = None) -> bool:
    path = db_path or get_db_path()
    return path.exists()
