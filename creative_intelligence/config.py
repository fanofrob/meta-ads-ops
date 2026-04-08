"""
Configuration for the creative intelligence subsystem.
Loads from the same .env as the main project, but reads only CI_ prefixed vars
plus the shared vars it needs (META_*, OPENAI_API_KEY).
Does not mutate or re-export anything from the main project's config.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_env() -> None:
    """Load .env if python-dotenv is available.

    Searches upward from the repo root so this works both in the main
    checkout and in git worktrees (where .env lives in the parent repo).
    """
    try:
        from dotenv import load_dotenv, find_dotenv
        # find_dotenv() walks up from CWD until it finds a .env file.
        env_path = find_dotenv(usecwd=True)
        if env_path:
            load_dotenv(env_path, override=True)
        else:
            # Fallback: try repo root relative to this file
            fallback = Path(__file__).parent.parent / ".env"
            if fallback.exists():
                load_dotenv(fallback, override=True)
    except ImportError:
        pass  # dotenv optional; env vars may already be set


_load_env()


# ─────────────────────────────────────────────
# Meta API (read-only, shared with main project)
# ─────────────────────────────────────────────

META_ACCESS_TOKEN: str = os.getenv("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID: str = os.getenv("META_AD_ACCOUNT_ID", "")
META_API_VERSION: str = os.getenv("META_API_VERSION", "v19.0")

# ─────────────────────────────────────────────
# LLM
# ─────────────────────────────────────────────

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
# Default model for creative generation. Can be overridden per call.
CI_LLM_MODEL: str = os.getenv("CI_LLM_MODEL", "gpt-4o-mini")
# Max tokens for generation responses.
CI_LLM_MAX_TOKENS: int = int(os.getenv("CI_LLM_MAX_TOKENS", "2000"))

# ─────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────

# Path to SQLite database.
CI_DB_PATH: str = os.getenv("CI_DB_PATH", "")

# Directory for generated operator reports.
CI_REPORTS_DIR: str = os.getenv(
    "CI_REPORTS_DIR",
    str(Path(__file__).parent.parent / "outputs" / "creative_reports"),
)

# ─────────────────────────────────────────────
# Data paths (read-only pointers to main project outputs)
# ─────────────────────────────────────────────

_raw_data_dir_env = os.getenv("CI_RAW_DATA_DIR", "").strip()
RAW_DATA_DIR: Path = (
    Path(_raw_data_dir_env)
    if _raw_data_dir_env
    else Path(__file__).parent.parent / "data" / "raw"
)

# ─────────────────────────────────────────────
# Product knowledge
# ─────────────────────────────────────────────

# Directory where product CSV/JSON/brief files can be dropped for ingestion.
CI_PRODUCT_KB_DIR: str = os.getenv(
    "CI_PRODUCT_KB_DIR",
    str(Path(__file__).parent.parent / "creative_intelligence_data" / "product_kb"),
)

# ─────────────────────────────────────────────
# Feature flags
# ─────────────────────────────────────────────

# Set CI_AI_TAGGING=1 to enable LLM-assisted tagging (costs tokens).
AI_TAGGING_ENABLED: bool = os.getenv("CI_AI_TAGGING", "0") == "1"

# Set CI_SHOPIFY_ENABLED=1 to enable Shopify product sync.
SHOPIFY_ENABLED: bool = os.getenv("CI_SHOPIFY_ENABLED", "0") == "1"

# ─────────────────────────────────────────────
# Shopify (read-only product sync)
# ─────────────────────────────────────────────

# e.g. "mystore.myshopify.com" or full URL "https://mystore.myshopify.com"
SHOPIFY_STORE_URL: str = os.getenv("SHOPIFY_STORE_URL", "")
# Admin API access token with read_products scope
SHOPIFY_ACCESS_TOKEN: str = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
# Admin API version to target (default: 2024-01)
SHOPIFY_API_VERSION: str = os.getenv("SHOPIFY_API_VERSION", "2024-01")


# ─────────────────────────────────────────────
# Static rendering / image generation (v1.4)
# ─────────────────────────────────────────────

# Provider name: "mock" (always safe) | "nano_banana" (requires API key)
CI_IMAGE_PROVIDER: str = os.getenv("CI_IMAGE_PROVIDER", "mock")

# Set to "1" to call the image provider API; default off (spec-only mode)
CI_IMAGE_GENERATION_ENABLED: bool = os.getenv("CI_IMAGE_GENERATION_ENABLED", "0") == "1"

# Local directory for generated image files (relative to repo root by default)
CI_RENDER_OUTPUT_DIR: str = os.getenv(
    "CI_RENDER_OUTPUT_DIR",
    str(Path(__file__).parent.parent / "creative_intelligence_data" / "render_outputs"),
)

# Nano Banana credentials — only needed when CI_IMAGE_PROVIDER=nano_banana
CI_NANO_BANANA_API_KEY: str = os.getenv("CI_NANO_BANANA_API_KEY", "")
CI_NANO_BANANA_ENDPOINT: str = os.getenv(
    "CI_NANO_BANANA_ENDPOINT",
    "https://api.nanobanana.io/v1/generate",   # placeholder — update from API docs
)


def validate() -> list[str]:
    """Return a list of missing required config vars."""
    missing = []
    if not META_ACCESS_TOKEN:
        missing.append("META_ACCESS_TOKEN")
    if not META_AD_ACCOUNT_ID:
        missing.append("META_AD_ACCOUNT_ID")
    return missing
