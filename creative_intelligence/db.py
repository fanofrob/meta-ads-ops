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
    ]
    for stmt in migrations:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # Column already exists


def db_exists(db_path: Path | None = None) -> bool:
    path = db_path or get_db_path()
    return path.exists()
