"""
Creative Intelligence — local test UI.

Fully isolated from the main Meta ops pipeline.
Runs as a standalone Flask app for internal creative testing.

Usage:
    cd /path/to/meta-ads-ops
    PYTHONPATH=. python creative_intelligence/webapp/app.py

Then open http://localhost:5555/test
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file, abort

# ─────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────

_HERE = Path(__file__).parent
app = Flask(__name__, template_folder=str(_HERE / "templates"))


def _db() -> sqlite3.Connection:
    """Open creative intelligence DB (initialises schema if needed)."""
    from creative_intelligence.db import get_connection, init_db
    init_db()  # no-op if tables already exist
    return get_connection()


def _outputs_dir() -> Path:
    """Return outputs/ dir at project root, creating if needed."""
    from creative_intelligence.db import get_db_path
    root = get_db_path().parent.parent  # creative_intelligence_data/../ = project root
    d = root / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_calibration_event(filename: str, record: dict[str, Any]) -> None:
    """Append a calibration event (pattern gap / false positive) to a JSON file."""
    path = _outputs_dir() / filename
    events: list[dict] = []
    if path.exists():
        try:
            events = json.loads(path.read_text())
        except Exception:
            events = []
    record["logged_at"] = datetime.utcnow().isoformat() + "Z"
    events.append(record)
    path.write_text(json.dumps(events, indent=2))


# ─────────────────────────────────────────────
# Scoring helper (structural + pattern match)
# ─────────────────────────────────────────────

def _predict_hook_score(
    hook_text: str,
    hook_type: str | None,
    angle: str | None,
    conn: sqlite3.Connection,
) -> float:
    """Quick predicted score for a newly generated hook (no historical performance)."""
    from creative_intelligence.scoring.scorer import _structural_score, _pattern_match_score
    from creative_intelligence.analysis.patterns import get_patterns
    from creative_intelligence.tagging.rule_tagger import tag_creative

    structural = _structural_score({"hook_text": hook_text})
    # Tag the actual hook text so scores reflect real content, not inherited pattern tags
    actual_tags = tag_creative({"hook_text": hook_text})
    tags = {
        "hook_type": actual_tags.get("hook_type") or hook_type or "unknown",
        "angle": actual_tags.get("angle") or angle or "unknown",
    }
    try:
        winning = get_patterns(min_winners=1, conn=conn)
    except Exception:
        winning = []
    pattern = _pattern_match_score(tags, winning)
    return round(structural * 0.5 + pattern * 0.5, 1)


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.get("/test")
def test_page() -> Any:
    return render_template("test.html")


@app.get("/api/products")
def api_products() -> Any:
    """Return one representative product per category, sorted by category name."""
    try:
        conn = _db()
        # Pick the product with the lowest id (most canonical) per category.
        # Products without a category fall back to their own name as the label.
        rows = conn.execute(
            """SELECT MIN(id) as id,
                      COALESCE(category, name) as label,
                      category,
                      AVG(price) as price
               FROM products
               WHERE active = 1
               GROUP BY COALESCE(category, name)
               ORDER BY label"""
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/generate")
def api_generate() -> Any:
    """Generate hooks with product context and return scored+tagged results."""
    data = request.get_json(force=True)
    product_id   = data.get("product_id") or None
    goal         = data.get("goal", "conversions")
    audience     = data.get("audience", "")
    angle_over   = (data.get("angle_override") or "").strip().lower()
    count        = max(1, min(int(data.get("count", 10)), 30))
    diversity    = data.get("diversity", "medium")
    tone         = data.get("tone", "authentic")

    try:
        from creative_intelligence.db import get_connection, init_db
        from creative_intelligence.analysis.patterns import get_patterns
        from creative_intelligence.generation.hook_generator import generate_hooks_from_pattern
        from creative_intelligence.tagging.rule_tagger import tag_creative as _tag_hook

        init_db()
        conn = get_connection()

        # Select patterns — filter by angle if override provided
        all_patterns = get_patterns(min_winners=1, conn=conn)
        if angle_over:
            filtered = [p for p in all_patterns
                        if angle_over in (p.get("angle") or "").lower()
                        or angle_over in (p.get("hook_type") or "").lower()]
            patterns = filtered[:3] if filtered else all_patterns[:3]
        else:
            patterns = all_patterns[:3]

        if not patterns:
            conn.close()
            return jsonify({
                "error": "No patterns found in DB. Run: ingest → tag → extract-patterns first."
            }), 400

        session_id = str(uuid.uuid4())
        hooks_per_pattern = max(1, count // len(patterns))
        all_hooks: list[dict[str, Any]] = []

        for pat in patterns:
            try:
                result = generate_hooks_from_pattern(
                    pattern_id=pat["id"],
                    count=hooks_per_pattern,
                    product_id=product_id,
                    dry_run=True,   # don't persist to generated_hooks
                    conn=conn,
                    diversity=diversity,
                    tone=tone,
                    goal=goal,
                    audience=audience,
                )
            except Exception as gen_exc:
                # Skip this pattern rather than aborting the whole request
                app.logger.warning("Pattern %s generation failed: %s", pat["id"], gen_exc)
                continue

            emotion = pat.get("emotional_trigger") or "unknown"

            for hook_text in result.get("hooks", []):
                # Tag the actual generated text — don't inherit pattern's stored tags
                # (all patterns in DB share the same tag since historical data was
                # uniformly labelled; real classification must come from the hook itself)
                actual_tags = _tag_hook({"hook_text": hook_text})
                hook_type = actual_tags.get("hook_type") or "unknown"
                angle_tag = actual_tags.get("angle") or "unknown"
                score = _predict_hook_score(hook_text, hook_type, angle_tag, conn)
                all_hooks.append({
                    "hook_text":      hook_text,
                    "predicted_score": score,
                    "angle":          angle_tag,
                    "emotion":        emotion,
                    "format":         "hook",
                    "pattern_name":   pat.get("pattern_name", ""),
                    "hook_type":      hook_type,
                })

        if not all_hooks:
            conn.close()
            return jsonify({"error": "Generation produced no hooks. Check LLM config."}), 500

        # Persist to creative_tests (so history panel works)
        inserted: list[dict[str, Any]] = []
        with conn:
            for h in all_hooks:
                cursor = conn.execute(
                    """INSERT INTO creative_tests
                         (session_id, product_id, goal, audience, angle_override,
                          hook_text, predicted_score, angle, emotion, format, pattern_name)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        session_id, product_id, goal, audience, angle_over or None,
                        h["hook_text"], h["predicted_score"],
                        h["angle"], h["emotion"], h["format"], h["pattern_name"],
                    ),
                )
                inserted.append({**h, "id": cursor.lastrowid, "session_id": session_id})

        conn.close()
        return jsonify({"session_id": session_id, "hooks": inserted})

    except Exception as exc:
        app.logger.exception("Generate failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/rate")
def api_rate() -> Any:
    """Submit a 1–5 star rating for a creative_test row."""
    data   = request.get_json(force=True)
    row_id = data.get("id")
    rating = data.get("rating")

    if not row_id or rating not in (1, 2, 3, 4, 5):
        return jsonify({"error": "id and rating (1-5) required"}), 400

    try:
        conn = _db()
        row = conn.execute(
            "SELECT hook_text, predicted_score FROM creative_tests WHERE id = ?",
            (row_id,),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Row not found"}), 404

        hook_text       = row["hook_text"]
        predicted_score = row["predicted_score"] or 0.0

        with conn:
            conn.execute(
                "UPDATE creative_tests SET human_rating = ? WHERE id = ?",
                (rating, row_id),
            )

        conn.close()

        # Calibration signal detection (predicted_score is 0–100; human_rating is 1–5)
        flagged = None
        if rating >= 4 and predicted_score < 30:
            flagged = "pattern_gap"
            _log_calibration_event("pattern_gaps.json", {
                "id": row_id,
                "hook_text": hook_text,
                "human_rating": rating,
                "predicted_score": predicted_score,
                "note": "Human liked it but system predicted low — potential pattern gap",
            })
        elif rating <= 2 and predicted_score >= 70:
            flagged = "false_positive"
            _log_calibration_event("false_positives.json", {
                "id": row_id,
                "hook_text": hook_text,
                "human_rating": rating,
                "predicted_score": predicted_score,
                "note": "System predicted high but human disliked it — false positive",
            })

        return jsonify({"ok": True, "flagged": flagged})

    except Exception as exc:
        app.logger.exception("Rate failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/flag")
def api_flag() -> Any:
    """Flag a hook as duplicate."""
    data   = request.get_json(force=True)
    row_id = data.get("id")
    if not row_id:
        return jsonify({"error": "id required"}), 400

    try:
        conn = _db()
        with conn:
            conn.execute(
                "UPDATE creative_tests SET is_duplicate = 1 WHERE id = ?", (row_id,)
            )
        conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/history")
def api_history() -> Any:
    """Return the last 20 rated/generated hooks with optional sorting."""
    sort   = request.args.get("sort", "newest")
    limit  = min(int(request.args.get("limit", 20)), 100)

    order_map = {
        "newest":           "created_at DESC",
        "highest_rated":    "human_rating DESC NULLS LAST, created_at DESC",
        "lowest_predicted": "predicted_score ASC NULLS LAST, created_at DESC",
    }
    order_clause = order_map.get(sort, "created_at DESC")

    try:
        conn = _db()
        rows = conn.execute(
            f"""SELECT t.id, t.session_id, t.product_id, t.goal, t.audience,
                       t.hook_text, t.predicted_score, t.human_rating,
                       t.angle, t.emotion, t.format, t.pattern_name,
                       t.is_duplicate, t.created_at,
                       p.name AS product_name
                FROM creative_tests t
                LEFT JOIN products p ON p.id = t.product_id
                ORDER BY {order_clause}
                LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Creative Copilot routes
# ─────────────────────────────────────────────

@app.get("/copilot")
def copilot_page() -> Any:
    return render_template("copilot.html")


@app.post("/api/copilot/session")
def copilot_session() -> Any:
    """Create a new copilot session and return its session_id."""
    data       = request.get_json(force=True)
    product_id = data.get("product_id") or None
    goal       = data.get("goal", "conversions")
    audience   = data.get("audience", "")
    tone       = data.get("tone", "authentic")

    session_id = str(uuid.uuid4())
    try:
        conn = _db()
        with conn:
            conn.execute(
                "INSERT INTO copilot_sessions (session_id, product_id, goal, audience, tone)"
                " VALUES (?,?,?,?,?)",
                (session_id, product_id, goal, audience, tone),
            )
        # Fetch product name for display
        product_name = None
        if product_id:
            row = conn.execute(
                "SELECT name FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            product_name = row["name"] if row else None
        conn.close()
        return jsonify({"session_id": session_id, "product_name": product_name})
    except Exception as exc:
        app.logger.exception("copilot/session failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/copilot/generate")
def copilot_generate() -> Any:
    """Initial concept entry for the copilot.

    If concept is provided → store it as-is (action_type=initial_generate).
    If concept is blank   → generate from top pattern using the existing engine.
    """
    from creative_intelligence.analysis.patterns import get_patterns
    from creative_intelligence.generation.hook_generator import generate_hooks_from_pattern
    from creative_intelligence.generation.copilot_actions import score_concept

    data       = request.get_json(force=True)
    session_id = data.get("session_id")
    concept    = (data.get("concept") or "").strip()
    product_id = data.get("product_id") or None
    goal       = data.get("goal", "conversions")
    audience   = data.get("audience", "")
    tone       = data.get("tone", "authentic")
    count      = max(1, min(int(data.get("count", 5)), 20))

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    try:
        conn = _db()
        iterations: list[dict[str, Any]] = []

        if concept:
            # Store manually-typed concept
            sc = score_concept(concept, conn)
            with conn:
                cur = conn.execute(
                    "INSERT INTO copilot_iterations"
                    " (session_id, product_id, action_type, concept_text, predicted_score, metadata)"
                    " VALUES (?,?,?,?,?,?)",
                    (session_id, product_id, "initial_generate", concept,
                     sc["overall"], json.dumps(sc)),
                )
            iterations.append({
                "id": cur.lastrowid, "session_id": session_id,
                "action_type": "initial_generate", "concept_text": concept,
                "predicted_score": sc["overall"], "is_favorite": 0,
                "metadata": sc,
            })
        else:
            # Generate from top pattern
            patterns = get_patterns(min_winners=1, conn=conn)
            if not patterns:
                conn.close()
                return jsonify({"error": "No patterns in DB. Run ingest → tag → extract-patterns first."}), 400

            pattern = patterns[0]
            result = generate_hooks_from_pattern(
                pattern_id=pattern["id"],
                count=count,
                product_id=product_id,
                dry_run=False,  # use real LLM; copilot stores in copilot_iterations not generated_hooks
                conn=conn,
                diversity="medium",
                tone=tone,
                goal=goal,
                audience=audience,
            )
            with conn:
                for hook_text in result.get("hooks", []):
                    sc = score_concept(hook_text, conn)
                    cur = conn.execute(
                        "INSERT INTO copilot_iterations"
                        " (session_id, product_id, action_type, concept_text, predicted_score, metadata)"
                        " VALUES (?,?,?,?,?,?)",
                        (session_id, product_id, "initial_generate", hook_text,
                         sc["overall"], json.dumps({**sc, "pattern_name": pattern.get("pattern_name", "")})),
                    )
                    iterations.append({
                        "id": cur.lastrowid, "session_id": session_id,
                        "action_type": "initial_generate", "concept_text": hook_text,
                        "predicted_score": sc["overall"], "is_favorite": 0,
                        "metadata": {**sc, "pattern_name": pattern.get("pattern_name", "")},
                    })

        conn.close()
        return jsonify({"iterations": iterations})

    except Exception as exc:
        app.logger.exception("copilot/generate failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/copilot/action")
def copilot_action() -> Any:
    """Apply a transformation action to a concept.

    Dispatches to copilot_actions.run_action() by action_type.
    Persists each output as a copilot_iteration with parent_id.
    """
    from creative_intelligence.generation.copilot_actions import run_action, score_concept

    data        = request.get_json(force=True)
    session_id  = data.get("session_id")
    parent_id   = data.get("iteration_id") or data.get("parent_id")
    concept     = (data.get("concept") or "").strip()
    action_type = (data.get("action_type") or "").strip()
    product_id  = data.get("product_id") or None
    params      = data.get("params") or {}

    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    if not concept:
        return jsonify({"error": "concept required"}), 400
    if not action_type:
        return jsonify({"error": "action_type required"}), 400

    try:
        conn = _db()

        action_result = run_action(
            action_type=action_type,
            concept=concept,
            product_id=product_id,
            params=params,
            conn=conn,
            dry_run=False,
        )

        results     = action_result["results"]
        is_rich     = action_type in ("ugc_concepts", "static_concepts", "script", "creator_brief")
        iterations: list[dict[str, Any]] = []

        _HOOK_ACTIONS = {"rewrite", "variants", "premium", "direct_response",
                         "curiosity", "mainstream", "adapt_audience"}

        with conn:
            for item in results:
                if action_type in _HOOK_ACTIONS:
                    # Plain hook string — score it
                    hook_text = item if isinstance(item, str) else str(item)
                    sc = score_concept(hook_text, conn)
                    predicted = sc["overall"]
                    meta = json.dumps(sc)
                    concept_text = hook_text
                else:
                    # Rich dict (ugc/static/script/brief) — store JSON
                    predicted = None
                    meta_dict = action_result.get("metadata", {})
                    if isinstance(item, dict):
                        meta_dict = {**meta_dict, **item}
                    meta = json.dumps(meta_dict)
                    # concept_text = hook field for UGC/static, else JSON dump
                    if action_type == "ugc_concepts" and isinstance(item, dict):
                        concept_text = item.get("hook", json.dumps(item))
                    elif action_type == "static_concepts" and isinstance(item, dict):
                        concept_text = item.get("headline", json.dumps(item))
                    elif action_type == "script" and isinstance(item, dict):
                        concept_text = item.get("hook", json.dumps(item))
                    elif action_type == "creator_brief" and isinstance(item, dict):
                        concept_text = item.get("hook_direction", json.dumps(item))
                    else:
                        concept_text = json.dumps(item) if isinstance(item, dict) else str(item)

                cur = conn.execute(
                    "INSERT INTO copilot_iterations"
                    " (session_id, parent_id, product_id, action_type,"
                    "  concept_text, predicted_score, metadata)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (session_id, parent_id, product_id, action_type,
                     concept_text, predicted, meta),
                )
                iterations.append({
                    "id": cur.lastrowid,
                    "session_id": session_id,
                    "parent_id": parent_id,
                    "action_type": action_type,
                    "concept_text": concept_text,
                    "predicted_score": predicted,
                    "is_favorite": 0,
                    "metadata": json.loads(meta),
                    "rich_data": item if is_rich else None,
                })

        conn.close()
        return jsonify({"iterations": iterations})

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("copilot/action failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/copilot/score")
def copilot_score() -> Any:
    """Score a concept without persisting to DB."""
    from creative_intelligence.generation.copilot_actions import score_concept

    data    = request.get_json(force=True)
    concept = (data.get("concept") or "").strip()
    if not concept:
        return jsonify({"error": "concept required"}), 400

    try:
        conn = _db()
        scores = score_concept(concept, conn)
        conn.close()
        return jsonify(scores)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/copilot/explain")
def copilot_explain() -> Any:
    """Explain pattern alignment for a concept (LLM call, persists result)."""
    from creative_intelligence.generation.copilot_actions import explain_concept

    data         = request.get_json(force=True)
    concept      = (data.get("concept") or "").strip()
    product_id   = data.get("product_id") or None
    iteration_id = data.get("iteration_id")

    if not concept:
        return jsonify({"error": "concept required"}), 400

    try:
        conn = _db()
        explanation = explain_concept(concept, product_id, conn)

        if iteration_id:
            with conn:
                conn.execute(
                    "INSERT INTO copilot_explanations (iteration_id, explanation)"
                    " VALUES (?,?)",
                    (iteration_id, explanation),
                )
        conn.close()
        return jsonify({"explanation": explanation})
    except Exception as exc:
        app.logger.exception("copilot/explain failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/copilot/favorite")
def copilot_favorite() -> Any:
    """Toggle is_favorite on a copilot iteration."""
    data         = request.get_json(force=True)
    iteration_id = data.get("iteration_id")
    favorite     = bool(data.get("favorite", True))

    if not iteration_id:
        return jsonify({"error": "iteration_id required"}), 400

    try:
        conn = _db()
        with conn:
            conn.execute(
                "UPDATE copilot_iterations SET is_favorite = ? WHERE id = ?",
                (1 if favorite else 0, iteration_id),
            )
        conn.close()
        return jsonify({"ok": True, "is_favorite": favorite})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/copilot/session/<session_id>")
def copilot_session_history(session_id: str) -> Any:
    """Return all iterations for a session, oldest first."""
    try:
        conn = _db()
        sess = conn.execute(
            "SELECT * FROM copilot_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not sess:
            conn.close()
            return jsonify({"error": "Session not found"}), 404

        iters = conn.execute(
            "SELECT i.*, p.name AS product_name"
            " FROM copilot_iterations i"
            " LEFT JOIN products p ON p.id = i.product_id"
            " WHERE i.session_id = ?"
            " ORDER BY i.created_at ASC",
            (session_id,),
        ).fetchall()
        conn.close()

        iterations = []
        for row in iters:
            d = dict(row)
            try:
                d["metadata"] = json.loads(d.get("metadata") or "{}")
            except Exception:
                d["metadata"] = {}
            iterations.append(d)

        return jsonify({"session": dict(sess), "iterations": iterations})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/copilot/sessions")
def copilot_sessions_list() -> Any:
    """Return the last 20 copilot sessions with iteration counts."""
    try:
        conn = _db()
        rows = conn.execute(
            """SELECT s.session_id, s.product_id, s.goal, s.audience, s.tone,
                      s.created_at, p.name AS product_name,
                      COUNT(i.id) AS iteration_count,
                      SUM(i.is_favorite) AS favorite_count
               FROM copilot_sessions s
               LEFT JOIN products p ON p.id = s.product_id
               LEFT JOIN copilot_iterations i ON i.session_id = s.session_id
               GROUP BY s.session_id
               ORDER BY s.created_at DESC
               LIMIT 20"""
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Admin — product sync
# ─────────────────────────────────────────────

@app.route("/admin/sync-products", methods=["POST"])
def admin_sync_products():
    """Trigger a Shopify → DB product sync. Protected by ADMIN_TOKEN env var."""
    import os as _os
    token = _os.getenv("ADMIN_TOKEN", "")
    req_token = request.get_json(force=True, silent=True) or {}
    if token and req_token.get("token") != token:
        return jsonify({"error": "unauthorized"}), 401
    try:
        from creative_intelligence.product_knowledge.shopify_adapter import run_sync
        result = run_sync(dry_run=False, fetch_metafields=False)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/admin/refresh-pipeline", methods=["POST"])
def admin_refresh_pipeline():
    """Run full refresh: Meta ingest → tag → extract-patterns → Shopify sync.
    Protected by ADMIN_TOKEN. Returns a summary of what ran."""
    import os as _os
    token = _os.getenv("ADMIN_TOKEN", "")
    req_token = request.get_json(force=True, silent=True) or {}
    if token and req_token.get("token") != token:
        return jsonify({"error": "unauthorized"}), 401

    summary: dict = {}
    conn = _db()
    try:
        # 1. Shopify sync
        from creative_intelligence.product_knowledge.shopify_adapter import run_sync
        shopify = run_sync(dry_run=False, fetch_metafields=False, conn=conn)
        summary["shopify_products"] = shopify.get("product_count", 0)
    except Exception as exc:
        summary["shopify_error"] = str(exc)

    try:
        # 2. Meta ingest
        from creative_intelligence.ingestion.meta_ingester import ingest_meta_ads
        ingested = ingest_meta_ads(conn=conn, dry_run=False)
        summary["meta_ingested"] = ingested
    except Exception as exc:
        summary["meta_error"] = str(exc)

    try:
        # 3. Tag untagged creatives
        from creative_intelligence.tagging.tagger import tag_creatives
        tagged = tag_creatives(conn=conn)
        summary["tagged"] = tagged
    except Exception as exc:
        summary["tagging_error"] = str(exc)

    try:
        # 4. Extract patterns
        from creative_intelligence.analysis.patterns import extract_patterns
        patterns = extract_patterns(conn=conn)
        summary["patterns_extracted"] = len(patterns) if patterns else 0
    except Exception as exc:
        summary["patterns_error"] = str(exc)

    conn.close()
    summary["completed_at"] = __import__("datetime").datetime.utcnow().isoformat()
    return jsonify(summary)


@app.route("/admin/import-data", methods=["POST"])
def admin_import_data():
    """Bulk-import rows into any table. Body: {token, table, rows: [...], truncate: bool}."""
    import os as _os
    token = _os.getenv("ADMIN_TOKEN", "")
    body = request.get_json(force=True, silent=True) or {}
    if token and body.get("token") != token:
        return jsonify({"error": "unauthorized"}), 401

    table = body.get("table", "").strip()
    rows = body.get("rows", [])
    truncate = bool(body.get("truncate", False))

    # Whitelist of importable tables
    allowed = {
        "creative_patterns", "creatives", "creative_performance",
        "creative_tags", "creative_scores",
    }
    if table not in allowed:
        return jsonify({"error": f"table '{table}' not in import whitelist"}), 400
    if not rows:
        return jsonify({"inserted": 0})

    try:
        from creative_intelligence.db import get_connection as _gc
        conn = _db()
        conn.execute("PRAGMA foreign_keys=OFF")
        with conn:
            if truncate:
                conn.execute(f"DELETE FROM {table}")
            cols = list(rows[0].keys())
            placeholders = ", ".join("?" for _ in cols)
            col_list = ", ".join(cols)
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
            conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()
        return jsonify({"inserted": len(rows), "table": table})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# PRODUCTION HANDOFF
# Turns approved copilot concepts into structured production-ready documents.
# ─────────────────────────────────────────────

@app.route("/api/production/generate", methods=["POST"])
def production_generate():
    """Generate (and optionally persist) a production output from an approved concept.

    Request JSON
    ------------
    {
      output_type       : "static_brief" | "ugc_brief" | "script_package" | "test_package"
      concept           : str    — the approved hook/concept text
      product_id        : str?   — product context
      session_id        : str?   — copilot session for context / variant pulling
      iteration_id      : int?   — source iteration (for lineage)
      audience          : str?   — audience description (test_package)
      goal              : str?   — campaign goal (test_package)
      dry_run           : bool?  — default False (persist to DB)
    }
    """
    from creative_intelligence.production.brief_builder import build_output
    try:
        body        = request.get_json(force=True) or {}
        output_type = body.get("output_type", "").strip()
        concept     = (body.get("concept") or "").strip()
        product_id  = body.get("product_id") or None
        session_id  = body.get("session_id") or None
        iter_id     = body.get("iteration_id") or None
        audience    = body.get("audience") or ""
        goal        = body.get("goal") or "conversions"
        dry_run     = bool(body.get("dry_run", False))

        if not output_type:
            return jsonify({"error": "output_type required"}), 400
        if not concept:
            return jsonify({"error": "concept required"}), 400

        conn   = _db()
        result = build_output(
            output_type=output_type,
            concept=concept,
            product_id=product_id,
            conn=conn,
            session_id=session_id,
            source_iteration_id=iter_id,
            audience=audience,
            goal=goal,
            dry_run=dry_run,
        )
        conn.close()
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/production/<int:output_id>", methods=["GET"])
def production_get(output_id):
    """Fetch a stored production output by ID."""
    import json as _json
    try:
        conn = _db()
        row  = conn.execute(
            "SELECT * FROM production_outputs WHERE id = ?", (output_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "not found"}), 404
        d = dict(row)
        try:
            d["output_json"] = _json.loads(d.get("output_json") or "{}")
        except Exception:
            pass
        return jsonify(d)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/production/<int:output_id>/approve", methods=["POST"])
def production_approve(output_id):
    """Toggle is_approved on a production output."""
    try:
        body       = request.get_json(force=True) or {}
        approved   = bool(body.get("approved", True))
        conn       = _db()
        conn.execute(
            "UPDATE production_outputs SET is_approved = ? WHERE id = ?",
            (1 if approved else 0, output_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "is_approved": approved})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/production/<int:output_id>/favorite", methods=["POST"])
def production_favorite(output_id):
    """Toggle is_favorite on a production output."""
    try:
        body    = request.get_json(force=True) or {}
        fav     = bool(body.get("favorite", True))
        conn    = _db()
        conn.execute(
            "UPDATE production_outputs SET is_favorite = ? WHERE id = ?",
            (1 if fav else 0, output_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "is_favorite": fav})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/production/<int:output_id>/export", methods=["GET"])
def production_export(output_id):
    """Export a production output in the requested format.

    Query params
    ------------
    format : md (default) | json | text
    """
    import json as _json
    from creative_intelligence.production.exporters import to_markdown, to_json, to_text_block
    try:
        fmt  = request.args.get("format", "md").lower()
        conn = _db()
        row  = conn.execute(
            "SELECT output_type, output_json, output_md FROM production_outputs WHERE id = ?",
            (output_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "not found"}), 404

        output_type = row["output_type"]
        try:
            data = _json.loads(row["output_json"] or "{}")
        except Exception:
            data = {}

        if fmt == "json":
            return app.response_class(
                to_json(data),
                status=200,
                mimetype="application/json",
            )
        elif fmt == "text":
            return app.response_class(
                to_text_block(output_type, data),
                status=200,
                mimetype="text/plain; charset=utf-8",
            )
        else:
            # md (default) — use pre-rendered if available, else re-render
            md = row["output_md"] or to_markdown(output_type, data)
            return app.response_class(
                md,
                status=200,
                mimetype="text/plain; charset=utf-8",
            )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/production/package/<int:pkg_id>/export", methods=["GET"])
def production_package_export(pkg_id):
    """Export a test package in the requested format."""
    import json as _json
    from creative_intelligence.production.exporters import to_markdown, to_json, to_text_block
    try:
        fmt  = request.args.get("format", "md").lower()
        conn = _db()
        row  = conn.execute(
            "SELECT * FROM production_packages WHERE id = ?", (pkg_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "not found"}), 404

        d = dict(row)
        # Reconstruct a TestPackage-shaped dict for exporters
        data = {
            "core_concept":      d.get("core_concept", ""),
            "core_iteration_id": d.get("core_iteration_id"),
            "variants":          _json.loads(d.get("variants_json") or "[]"),
            "audience":          d.get("audience", ""),
            "goal":              d.get("goal", "conversions"),
            "pattern_alignment": _json.loads(d.get("pattern_alignment_json") or "{}"),
            "score_summary":     _json.loads(d.get("score_summary_json") or "{}"),
            "product_context":   "",
        }

        if fmt == "json":
            return app.response_class(
                to_json(data), status=200, mimetype="application/json"
            )
        elif fmt == "text":
            return app.response_class(
                to_text_block("test_package", data),
                status=200,
                mimetype="text/plain; charset=utf-8",
            )
        else:
            md = d.get("output_md") or to_markdown("test_package", data)
            return app.response_class(
                md, status=200, mimetype="text/plain; charset=utf-8"
            )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/production/list", methods=["GET"])
def production_list():
    """List the 20 most recent production outputs and packages."""
    import json as _json
    try:
        conn = _db()

        # Single-concept outputs
        output_rows = conn.execute(
            """SELECT po.id, po.output_type, po.concept_text,
                      po.is_approved, po.is_favorite, po.created_at,
                      p.name AS product_name
               FROM production_outputs po
               LEFT JOIN products p ON p.id = po.product_id
               ORDER BY po.created_at DESC LIMIT 20"""
        ).fetchall()

        # Test packages
        pkg_rows = conn.execute(
            """SELECT pp.id, 'test_package' AS output_type, pp.core_concept AS concept_text,
                      pp.is_approved, pp.is_favorite, pp.created_at,
                      p.name AS product_name
               FROM production_packages pp
               LEFT JOIN products p ON p.id = pp.product_id
               ORDER BY pp.created_at DESC LIMIT 20"""
        ).fetchall()

        conn.close()

        items = sorted(
            [dict(r) for r in output_rows] + [dict(r) for r in pkg_rows],
            key=lambda x: x.get("created_at") or "",
            reverse=True,
        )[:20]

        return jsonify(items)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# STATIC RENDERING LAYER  (v1.4)
# Converts StaticAdBrief production outputs into render specs + image variants.
# ─────────────────────────────────────────────

@app.get("/render")
def render_page() -> Any:
    """Static rendering review UI."""
    return render_template("render.html")


@app.post("/api/render/generate")
def render_generate() -> Any:
    """
    Generate render specs (and optionally images) from a stored StaticAdBrief.

    Request JSON
    ------------
    {
      production_output_id : int    — required
      aspect_ratio         : str?   — "9:16" | "1:1" | "4:5" | "16:9"  (default: "9:16")
      variants             : list?  — subset of VARIANT_STRATEGIES (default: all 5)
      generate_images      : bool?  — override CI_IMAGE_GENERATION_ENABLED
      model                : str?   — Replicate model slug override (e.g. "black-forest-labs/flux-1.1-pro")
      dry_run              : bool?  — default False
    }
    """
    from creative_intelligence.rendering.static_renderer import render_static_brief
    from creative_intelligence.rendering.schemas import VARIANT_STRATEGIES

    try:
        body   = request.get_json(force=True) or {}
        pid    = body.get("production_output_id")
        if not pid:
            return jsonify({"error": "production_output_id required"}), 400

        aspect        = body.get("aspect_ratio", "9:16")
        variants_req  = body.get("variants") or list(VARIANT_STRATEGIES)
        gen_images    = body.get("generate_images")   # None = use config
        model         = body.get("model") or None
        dry_run       = bool(body.get("dry_run", False))

        conn   = _db()
        result = render_static_brief(
            production_output_id=int(pid),
            conn=conn,
            variants=tuple(v for v in variants_req if v in VARIANT_STRATEGIES),
            aspect_ratio=aspect,
            generate_images=gen_images,
            model=model,
            dry_run=dry_run,
        )
        conn.close()
        return jsonify(result)

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        app.logger.exception("render/generate failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/render/<int:render_id>")
def render_fetch(render_id: int) -> Any:
    """Fetch a render output with its spec and all assets."""
    try:
        from creative_intelligence.rendering.asset_store import list_render_assets
        conn = _db()
        row  = conn.execute(
            "SELECT * FROM render_outputs WHERE id = ?", (render_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "render output not found"}), 404

        d = dict(row)
        try:
            d["render_specs"] = json.loads(d.get("render_spec_json") or "[]")
        except Exception:
            d["render_specs"] = []

        assets = list_render_assets(render_id, conn)
        conn.close()
        return jsonify({**d, "assets": assets})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/<int:render_id>/approve")
def render_approve(render_id: int) -> Any:
    """Toggle is_approved on a render output."""
    try:
        conn = _db()
        row  = conn.execute(
            "SELECT is_approved FROM render_outputs WHERE id = ?", (render_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        new_val = 0 if row["is_approved"] else 1
        with conn:
            conn.execute(
                "UPDATE render_outputs SET is_approved = ? WHERE id = ?",
                (new_val, render_id),
            )
        conn.close()
        return jsonify({"ok": True, "is_approved": bool(new_val)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/<int:render_id>/favorite")
def render_favorite(render_id: int) -> Any:
    """Toggle is_favorite on a render output."""
    try:
        conn = _db()
        row  = conn.execute(
            "SELECT is_favorite FROM render_outputs WHERE id = ?", (render_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        new_val = 0 if row["is_favorite"] else 1
        with conn:
            conn.execute(
                "UPDATE render_outputs SET is_favorite = ? WHERE id = ?",
                (new_val, render_id),
            )
        conn.close()
        return jsonify({"ok": True, "is_favorite": bool(new_val)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/asset/<int:asset_id>/review")
def render_asset_review(asset_id: int) -> Any:
    """
    Set review_status on a render asset.
    Body: {"status": "preferred" | "rejected" | "pending"}
    """
    try:
        body   = request.get_json(force=True) or {}
        status = (body.get("status") or "pending").strip().lower()
        if status not in ("preferred", "rejected", "pending"):
            return jsonify({"error": "status must be preferred|rejected|pending"}), 400
        conn = _db()
        row  = conn.execute(
            "SELECT id FROM render_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "asset not found"}), 404
        with conn:
            conn.execute(
                "UPDATE render_assets SET review_status = ? WHERE id = ?",
                (status, asset_id),
            )
        conn.close()
        return jsonify({"ok": True, "review_status": status})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/render/asset/<int:asset_id>/image")
def render_asset_image(asset_id: int) -> Any:
    """Serve a render asset image.

    Priority order:
    1. If path is an HTTP URL → 302 redirect (no local copy needed)
    2. If path is a local file that exists → send_file
    3. If local file missing → try metadata cdn_url redirect
    4. Otherwise 404
    """
    from flask import redirect as _redirect
    try:
        conn = _db()
        row = conn.execute(
            "SELECT asset_path_or_url, metadata_json FROM render_assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return ("", 404)

        path     = row["asset_path_or_url"] or ""
        meta_raw = row["metadata_json"]
        metadata: dict = {}
        if meta_raw:
            try:
                metadata = json.loads(meta_raw)
            except Exception:
                metadata = {}

        # 1. HTTP/HTTPS URL stored directly → redirect browser to CDN
        if path.startswith("http://") or path.startswith("https://"):
            return _redirect(path, code=302)

        # 2. Local file path
        if path and not path.startswith("mock://"):
            p = Path(path)
            _exists = p.exists()
            _fsize  = p.stat().st_size if _exists else -1
            app.logger.info(
                "render_asset_image %s: path=%s exists=%s size=%s",
                asset_id, path, _exists, _fsize,
            )
            if _exists:
                with open(p, "rb") as _f:
                    _hdr = _f.read(12)
                # Detect MIME type from magic bytes; fall back to jpeg for
                # unknown formats (browser content-sniffing handles the rest).
                # Reject only tiny files that are clearly not images (< 2KB).
                if _fsize < 2048:
                    app.logger.warning(
                        "render_asset_image %s: file too small (%s bytes), "
                        "treating as corrupt", asset_id, _fsize
                    )
                else:
                    if _hdr.startswith(b"\x89PNG"):
                        mime = "image/png"
                    elif _hdr.startswith(b"\xff\xd8\xff"):
                        mime = "image/jpeg"
                    elif _hdr[:4] == b"RIFF" and _hdr[8:12] == b"WEBP":
                        mime = "image/webp"
                    else:
                        # Unknown format — serve as image/jpeg; modern browsers
                        # sniff the real content type from the bytes regardless.
                        app.logger.warning(
                            "render_asset_image %s: unknown magic=%s size=%s, "
                            "serving as image/jpeg for browser sniff",
                            asset_id, _hdr[:8].hex(), _fsize,
                        )
                        mime = "image/jpeg"
                    return send_file(str(p), mimetype=mime)
            # File gone or corrupt → try CDN fallback from metadata
            cdn_url = metadata.get("cdn_url") or ""
            if cdn_url.startswith("http"):
                return _redirect(cdn_url, code=302)

        return ("", 404)
    except Exception as exc:
        from werkzeug.exceptions import HTTPException as _HTTPExc
        if isinstance(exc, _HTTPExc):
            raise  # let Flask handle HTTP exceptions normally
        app.logger.exception("render_asset_image %s failed", asset_id)
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/asset/<int:asset_id>/approve")
def render_asset_approve(asset_id: int) -> Any:
    """Set review_status='approved' and record reviewed_at timestamp."""
    try:
        from datetime import datetime as _dt
        conn = _db()
        row = conn.execute(
            "SELECT id FROM render_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "asset not found"}), 404
        now = _dt.utcnow().isoformat()
        with conn:
            conn.execute(
                "UPDATE render_assets SET review_status = 'approved', reviewed_at = ? WHERE id = ?",
                (now, asset_id),
            )
        conn.close()
        return jsonify({"ok": True, "review_status": "approved", "reviewed_at": now})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/asset/<int:asset_id>/reject")
def render_asset_reject(asset_id: int) -> Any:
    """Set review_status='rejected' and record reviewed_at timestamp."""
    try:
        from datetime import datetime as _dt
        conn = _db()
        row = conn.execute(
            "SELECT id FROM render_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "asset not found"}), 404
        now = _dt.utcnow().isoformat()
        with conn:
            conn.execute(
                "UPDATE render_assets SET review_status = 'rejected', reviewed_at = ? WHERE id = ?",
                (now, asset_id),
            )
        conn.close()
        return jsonify({"ok": True, "review_status": "rejected", "reviewed_at": now})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/asset/<int:asset_id>/favorite")
def render_asset_favorite(asset_id: int) -> Any:
    """Toggle is_favorite on a render asset."""
    try:
        conn = _db()
        row = conn.execute(
            "SELECT is_favorite FROM render_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "asset not found"}), 404
        new_val = 0 if row["is_favorite"] else 1
        with conn:
            conn.execute(
                "UPDATE render_assets SET is_favorite = ? WHERE id = ?",
                (new_val, asset_id),
            )
        conn.close()
        return jsonify({"ok": True, "is_favorite": bool(new_val)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/asset/<int:asset_id>/ready")
def render_asset_ready(asset_id: int) -> Any:
    """Toggle is_ready_to_test on a render asset."""
    try:
        conn = _db()
        row = conn.execute(
            "SELECT is_ready_to_test FROM render_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "asset not found"}), 404
        new_val = 0 if row["is_ready_to_test"] else 1
        with conn:
            conn.execute(
                "UPDATE render_assets SET is_ready_to_test = ? WHERE id = ?",
                (new_val, asset_id),
            )
        conn.close()
        return jsonify({"ok": True, "is_ready_to_test": bool(new_val)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/asset/<int:asset_id>/notes")
def render_asset_notes(asset_id: int) -> Any:
    """Set review_notes on a render asset. Body: {"notes": "..."}"""
    try:
        body  = request.get_json(force=True) or {}
        notes = body.get("notes", "")
        conn  = _db()
        row   = conn.execute(
            "SELECT id FROM render_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "asset not found"}), 404
        with conn:
            conn.execute(
                "UPDATE render_assets SET review_notes = ? WHERE id = ?",
                (notes, asset_id),
            )
        conn.close()
        return jsonify({"ok": True, "review_notes": notes})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/render/asset/<int:asset_id>/regenerate")
def render_asset_regenerate(asset_id: int) -> Any:
    """
    Regenerate a single variant image, optionally with an edited prompt.

    Body (all optional):
      {
        "visual_prompt":    str   — override the visual prompt
        "negative_prompt":  str   — override the negative prompt
        "model":            str   — Replicate model slug override
        "extra_notes":      str   — appended to the visual prompt
      }

    Returns the new render_assets row id and asset path/URL.
    """
    from creative_intelligence.rendering.asset_store import save_asset
    from creative_intelligence.rendering.providers import get_provider

    try:
        body = request.get_json(force=True) or {}
        conn = _db()

        # Fetch the source asset to get variant context
        row = conn.execute(
            """SELECT ra.*, ro.render_spec_json, ro.provider_name,
                      ro.source_production_output_id
               FROM render_assets ra
               JOIN render_outputs ro ON ro.id = ra.render_output_id
               WHERE ra.id = ?""",
            (asset_id,),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "asset not found"}), 404

        d = dict(row)
        render_output_id = d["render_output_id"]
        variant_label    = d["variant_label"]

        # Find the matching spec for this variant
        specs = json.loads(d.get("render_spec_json") or "[]")
        spec  = next((s for s in specs if s.get("variant_label") == variant_label), {})

        # Allow caller to override prompts
        visual_prompt    = body.get("visual_prompt")    or spec.get("visual_prompt", "")
        negative_prompt  = body.get("negative_prompt")  or spec.get("negative_prompt", "")
        extra_notes      = (body.get("extra_notes") or "").strip()
        if extra_notes:
            visual_prompt = f"{visual_prompt}. {extra_notes}"

        model            = body.get("model") or d.get("provider_name") or None
        aspect_ratio     = spec.get("aspect_ratio", "9:16")

        provider = get_provider(model)
        paths    = provider.generate(
            prompt=visual_prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
        )

        if not paths:
            conn.close()
            return jsonify({"error": "provider returned no output"}), 500

        new_asset_id = save_asset(
            render_output_id=render_output_id,
            variant_label=variant_label,
            source=paths[0],
            conn=conn,
            metadata={
                "concept_title":    spec.get("concept_title", ""),
                "aspect_ratio":     aspect_ratio,
                "edited_prompt":    True,
                "custom_notes":     extra_notes,
                "base_asset_id":    asset_id,
            },
        )
        new_row = conn.execute(
            "SELECT * FROM render_assets WHERE id = ?", (new_asset_id,)
        ).fetchone()
        conn.close()
        return jsonify({"ok": True, "asset": dict(new_row), "new_asset_id": new_asset_id})

    except Exception as exc:
        app.logger.exception("regenerate failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/render/summary/<int:render_output_id>")
def render_summary(render_output_id: int) -> Any:
    """Return decision summary counts for all assets of a render output."""
    try:
        conn = _db()
        row = conn.execute(
            "SELECT id FROM render_outputs WHERE id = ?", (render_output_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "render output not found"}), 404
        assets = conn.execute(
            "SELECT id, variant_label, review_status, is_favorite, is_ready_to_test, asset_path_or_url "
            "FROM render_assets WHERE render_output_id = ?",
            (render_output_id,),
        ).fetchall()
        conn.close()
        asset_list = [dict(a) for a in assets]
        return jsonify({
            "render_output_id": render_output_id,
            "total":        len(asset_list),
            "approved":     sum(1 for a in asset_list if a["review_status"] == "approved"),
            "rejected":     sum(1 for a in asset_list if a["review_status"] == "rejected"),
            "favorites":    sum(1 for a in asset_list if a["is_favorite"]),
            "ready_to_test": sum(1 for a in asset_list if a["is_ready_to_test"]),
            "assets": asset_list,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/render/list")
def render_list() -> Any:
    """Return the last 20 render outputs with source brief info."""
    try:
        conn = _db()
        rows = conn.execute(
            """SELECT r.id, r.source_production_output_id, r.render_type,
                      r.status, r.provider_name, r.is_approved, r.is_favorite,
                      r.created_at,
                      po.concept_text, po.output_type,
                      p.name AS product_name
               FROM render_outputs r
               LEFT JOIN production_outputs po ON po.id = r.source_production_output_id
               LEFT JOIN products p ON p.id = po.product_id
               ORDER BY r.created_at DESC
               LIMIT 20"""
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Collection routes
# ─────────────────────────────────────────────

@app.get("/collection")
def collection_page() -> Any:
    """Creative collection: all approved, favorited, and ready-to-test assets."""
    return render_template("collection.html")


@app.get("/api/collection")
def collection_api() -> Any:
    """Return all approved/favorite/ready assets with their context."""
    try:
        conn = _db()
        rows = conn.execute(
            """SELECT
                ra.id as asset_id,
                ra.variant_label,
                ra.asset_path_or_url,
                ra.metadata_json,
                ra.review_status,
                ra.is_favorite,
                ra.is_ready_to_test,
                ra.review_notes,
                ra.reviewed_at,
                ro.id as render_output_id,
                ro.source_production_output_id,
                ro.created_at,
                po.concept_text,
                p.name as product_name
            FROM render_assets ra
            JOIN render_outputs ro ON ro.id = ra.render_output_id
            LEFT JOIN production_outputs po ON po.id = ro.source_production_output_id
            LEFT JOIN products p ON p.id = po.product_id
            WHERE ra.review_status = 'approved' OR ra.is_favorite = 1 OR ra.is_ready_to_test = 1
            ORDER BY ra.is_ready_to_test DESC, ra.is_favorite DESC, ra.id DESC"""
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Railway injects PORT; CI_WEB_PORT used locally.
    port = int(os.getenv("PORT", os.getenv("CI_WEB_PORT", "5555")))
    # Bind to 0.0.0.0 so Railway (and other hosts) can reach the app.
    # debug=False in production; keep True only when PORT is not set externally.
    is_local = not os.getenv("PORT")
    print(f"Creative Test Lab starting on      http://localhost:{port}/test")
    print(f"Creative Copilot starting on       http://localhost:{port}/copilot")
    print(f"Production API available at        http://localhost:{port}/api/production/")
    print(f"Static Render Review at            http://localhost:{port}/render")
    app.run(host="0.0.0.0", port=port, debug=is_local)
