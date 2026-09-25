"""
Meta → winning patterns refresh, runnable from the web app.

Chains the existing read-only pipeline:

  src/fetch_campaigns.py, fetch_adsets.py, fetch_ads.py, fetch_insights.py
      → data/raw/*.json                      (GET only, logged to outputs/api.log)
  src/fetch_creatives.py --window 30d → ad copy for ads that spent in the window
  ci ingest --skip-copy-fetch → creatives + creative_performance (from data/raw)
  ci tag      → rule-based tags
  ci extract-patterns --date-range 30d → creative_patterns

Every step runs as a subprocess: the src/ scripts sys.exit() on API errors,
which must not take the web server down with them. Nothing here calls a Meta
write endpoint. Progress is recorded in meta_sync_runs so the UI can poll it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent.parent
DATE_RANGE = "30d"

STEPS: list[tuple[str, list[str]]] = [
    ("Fetching campaigns", ["src/fetch_campaigns.py"]),
    ("Fetching ad sets", ["src/fetch_adsets.py"]),
    ("Fetching ads", ["src/fetch_ads.py"]),
    ("Fetching yesterday's results", ["src/fetch_insights.py"]),
    ("Fetching 7-day results", ["src/fetch_insights.py", "--last7d"]),
    ("Fetching 30-day results", ["src/fetch_insights.py", "--historical"]),
    ("Fetching ad copy", ["src/fetch_creatives.py", "--window", DATE_RANGE]),
    ("Importing ads + copy", ["-m", "creative_intelligence.cli", "ingest", "--skip-copy-fetch"]),
    ("Tagging ads", ["-m", "creative_intelligence.cli", "tag"]),
    ("Finding winning patterns", ["-m", "creative_intelligence.cli", "extract-patterns",
                                  "--date-range", DATE_RANGE]),
]

_lock = threading.Lock()


def _now() -> str:
    return datetime.utcnow().isoformat()


def friendly_error(output: str) -> str:
    """Turn a failed step's output into something the operator can act on."""
    low = output.lower()
    if "code 190" in low or "error validating access token" in low or "session has been invalidated" in low:
        return ("Meta access token is invalid or expired. Create a new token with ads_read "
                "access and put it in .env as META_ACCESS_TOKEN, then sync again (no restart needed).")
    if "missing required environment variables" in low:
        return "Meta credentials are missing from .env (META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_API_VERSION)."
    if "code 17" in low or "code 4)" in low or "rate limit" in low:
        return "Meta rate limit hit — wait a few minutes and sync again."
    tail = [ln for ln in output.strip().splitlines() if ln.strip()][-3:]
    return " ".join(tail)[-400:] or "step failed"


def repair_null_ids(conn: Any) -> int:
    """
    Drop legacy creatives rows whose id is NULL but whose ad now also exists
    with a real creative id (re-imported by this sync). Rows without a
    replacement are kept. Returns the number removed.
    """
    with conn:
        cur = conn.execute(
            "DELETE FROM creatives WHERE id IS NULL AND ad_id IN"
            " (SELECT ad_id FROM creatives WHERE id IS NOT NULL)")
        conn.execute("DELETE FROM creative_tags WHERE creative_id IS NULL")
    return cur.rowcount


def _step_env() -> dict[str, str]:
    """
    The web server's environment with .env re-read on top, so a token pasted
    into .env while the app is running is used. (load_dotenv in the child
    never overrides a variable it inherited, so the stale value would win.)
    """
    from dotenv import dotenv_values
    env = os.environ.copy()
    env.update({k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None})
    return env


def _run_step(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True, text=True,
                       timeout=1800, env=_step_env())
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_sync(conn_factory: Callable[[], Any], run_id: int,
             runner: Callable[[list[str]], tuple[int, str]] = _run_step) -> None:
    """Run every step for meta_sync_runs row `run_id`, recording progress."""
    def update(**kw: Any) -> None:
        conn = conn_factory()
        with conn:
            conn.execute(f"UPDATE meta_sync_runs SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?",
                         (*kw.values(), run_id))
        conn.close()

    try:
        for i, (label, argv) in enumerate(STEPS, 1):
            update(step=f"{i}/{len(STEPS)} · {label}")
            code, out = runner(argv)
            if code != 0:
                update(status="failed", finished_at=_now(), message=f"{label}: {friendly_error(out)}")
                return
            if "ingest" in argv:
                conn = conn_factory()
                repair_null_ids(conn)
                conn.close()
        conn = conn_factory()
        started = conn.execute("SELECT started_at FROM meta_sync_runs WHERE id=?",
                               (run_id,)).fetchone()[0]
        perf = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(spend), 0) FROM creative_performance"
            " WHERE date_range=? AND snapshot_date=(SELECT MAX(snapshot_date)"
            " FROM creative_performance WHERE date_range=?)", (DATE_RANGE, DATE_RANGE)).fetchone()
        n_pat = conn.execute("SELECT COUNT(*) FROM creative_patterns WHERE updated_at>=?",
                             (started,)).fetchone()[0]
        conn.close()
        update(status="done", finished_at=_now(), step="", n_ads=perf[0], spend=perf[1],
               n_patterns=n_pat,
               message="" if n_pat else "Sync finished but no pattern had enough winners.")
    except Exception as exc:  # never leave a run stuck at 'running'
        update(status="failed", finished_at=_now(), message=str(exc)[:400])
    finally:
        if _lock.locked():
            _lock.release()


def start_sync(conn_factory: Callable[[], Any]) -> tuple[int | None, str]:
    """Start a background sync. Returns (run_id, '') or (None, reason)."""
    if not _lock.acquire(blocking=False):
        return None, "A sync is already running."
    try:
        conn = conn_factory()
        with conn:
            conn.execute("UPDATE meta_sync_runs SET status='failed', message='interrupted'"
                         " WHERE status='running'")
            cur = conn.execute("INSERT INTO meta_sync_runs (started_at, date_range, step)"
                               " VALUES (?,?, 'starting')", (_now(), DATE_RANGE))
        run_id = cur.lastrowid
        conn.close()
    except Exception:
        _lock.release()
        raise
    threading.Thread(target=run_sync, args=(conn_factory, run_id), daemon=True).start()
    return run_id, ""


def latest_run(conn: Any, status: str | None = None) -> dict[str, Any] | None:
    sql = "SELECT * FROM meta_sync_runs"
    params: tuple = ()
    if status:
        sql += " WHERE status=?"
        params = (status,)
    r = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    return dict(r) if r else None
