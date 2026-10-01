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

from flask import Flask, jsonify, make_response, render_template, request, send_file, abort

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


def _clean_product_display_name(raw_name: str, category: str | None) -> str:
    """Return a clean display name for product dropdowns.

    Priority:
    1. Category un-inverted:  "Fruit: Orange, Blood" → "Fruit: Blood Orange"
    2. Raw name stripped of weight/SKU/offer tokens: "1LB Kumquat 50% Off" → "Kumquat"
    """
    import re as _re
    if category:
        colon = category.find(":")
        if colon != -1:
            prefix = category[:colon + 1]           # "Fruit:"
            rest   = category[colon + 1:].strip()   # "Orange, Blood"
            parts  = [p.strip() for p in rest.split(",") if p.strip()]
            name   = " ".join(reversed(parts)) if len(parts) > 1 else rest
            return f"{prefix} {name}"               # "Fruit: Blood Orange"
        return category
    # No category — strip weight/offer noise from raw Shopify name
    name = _re.sub(r"^\d+(\.\d+)?\s*(lb|lbs|oz|g|kg|pound|pounds)\b[\s\-]*", "", raw_name, flags=_re.I)
    name = _re.sub(r"[\s\-]*([\d]+%\s*off|sale|deal|promo|discount|free\s*shipping)[\s\S]*$", "", name, flags=_re.I)
    return name.strip() or raw_name


@app.get("/api/products")
def api_products() -> Any:
    """Return one representative product per category, sorted by category name."""
    try:
        conn = _db()
        rows = conn.execute(
            """SELECT MIN(id) as id,
                      COALESCE(category, name) as label,
                      category,
                      MIN(name) as raw_name,
                      AVG(price) as price,
                      MIN(image_url) as image_url,
                      MIN(short_description) as short_description
               FROM products
               WHERE active = 1
               GROUP BY COALESCE(category, name)
               ORDER BY label"""
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            d["display_name"] = _clean_product_display_name(d["raw_name"], d.get("category"))
            results.append(d)
        return jsonify(results)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/products/<product_id>", methods=["GET"])
def api_product_detail(product_id: str) -> Any:
    """Return full detail for a single product."""
    try:
        conn = _db()
        row = conn.execute(
            "SELECT * FROM products WHERE id = ? LIMIT 1", (product_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Product not found"}), 404
        return jsonify(dict(row))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/products/<product_id>", methods=["PATCH"])
def api_product_update(product_id: str) -> Any:
    """Update editable product fields. Currently supports: image_url, short_description."""
    try:
        data = request.get_json(force=True) or {}
        allowed = {"image_url", "short_description", "description", "positioning"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"error": "No updatable fields provided"}), 400
        conn = _db()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [product_id]
        with conn:
            conn.execute(
                f"UPDATE products SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                values,
            )
        conn.close()
        return jsonify({"ok": True, "updated": list(updates.keys())})
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
    data                = request.get_json(force=True)
    product_id          = data.get("product_id") or None
    custom_product_name = (data.get("custom_product_name") or "").strip()
    goal                = data.get("goal", "conversions")
    audience            = data.get("audience", "")
    tone                = data.get("tone", "authentic")

    session_id = str(uuid.uuid4())
    try:
        conn = _db()
        with conn:
            conn.execute(
                "INSERT INTO copilot_sessions (session_id, product_id, goal, audience, tone)"
                " VALUES (?,?,?,?,?)",
                (session_id, product_id, goal, audience, tone),
            )
        # Resolve display name: Shopify product → DB lookup; custom → use as-is
        product_name = custom_product_name or None
        if product_id and not product_name:
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

    data                = request.get_json(force=True)
    session_id          = data.get("session_id")
    concept             = (data.get("concept") or "").strip()
    product_id          = data.get("product_id") or None
    custom_product_name = (data.get("custom_product_name") or "").strip()
    goal                = data.get("goal", "conversions")
    audience            = data.get("audience", "")
    tone                = data.get("tone", "authentic")
    count               = max(1, min(int(data.get("count", 5)), 20))

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
            # Generate from top pattern — fall back to direct generation if DB is empty
            patterns = get_patterns(min_winners=1, conn=conn)

            # Build a minimal context block for custom (non-Shopify) products.
            # IMPORTANT: first line must start with "Name:" so that
            # _product_name_from_context() in hook_generator.py can parse it.
            custom_ctx = (
                f"Name: {custom_product_name}\n"
                f"This product is not yet in the Shopify catalogue — use the name and any "
                f"audience/goal context to infer desires and write specific, vivid hooks."
            ) if custom_product_name and not product_id else ""

            if patterns:
                # Pattern-guided generation (normal path).
                # Rotate through patterns per session so different sessions explore
                # different creative territories rather than always using the top pattern.
                pattern_idx = hash(session_id or "") % len(patterns)
                pattern = patterns[pattern_idx]
                result = generate_hooks_from_pattern(
                    pattern_id=pattern["id"],
                    count=count,
                    product_id=product_id,
                    dry_run=True,  # copilot saves to copilot_iterations — skip double-write
                    conn=conn,
                    diversity="medium",
                    tone=tone,
                    goal=goal,
                    audience=audience,
                    custom_product_context=custom_ctx or None,
                )
                hooks_rich = result.get("hooks_rich", [])
                pattern_name = pattern.get("pattern_name", "")
            else:
                # No patterns yet — generate directly from product context
                from creative_intelligence.generation.copilot_actions import run_action as _run_action
                from creative_intelligence.product_knowledge.enricher import build_prompt_context_block
                product_ctx = (
                    custom_ctx
                    or (build_prompt_context_block(product_id, conn) if product_id else "")
                )
                seed = (
                    f"Generate {count} direct-response Meta ad hooks for: {custom_product_name}."
                    if custom_product_name and not product_id
                    else (
                        f"Generate {count} direct-response Meta ad hooks for this product."
                        if not product_ctx else product_ctx.split("\n")[0]
                    )
                )
                action_result = _run_action(
                    action_type="variants",
                    concept=seed,
                    product_id=product_id,
                    params={"count": count, "tone": tone},
                    conn=conn,
                    dry_run=True,
                )
                hooks_rich = action_result.get("hooks_rich", [])
                pattern_name = ""

            with conn:
                for rich in hooks_rich:
                    hook_text = rich.get("text", "") if isinstance(rich, dict) else str(rich)
                    if not hook_text:
                        continue
                    sc = score_concept(hook_text, conn)
                    # Attach D+C+PS metadata to the copilot iteration
                    meta: dict[str, Any] = {**sc, "pattern_name": pattern_name}
                    if isinstance(rich, dict):
                        if rich.get("archetype"): meta["archetype"]   = rich["archetype"]
                        if rich.get("d"):         meta["dcp_d"]       = rich["d"]
                        if rich.get("c"):         meta["dcp_c"]       = rich["c"]
                        if rich.get("ps"):        meta["dcp_ps"]      = rich["ps"]
                        if rich.get("clarity"):   meta["dcp_clarity"] = rich["clarity"]
                    cur = conn.execute(
                        "INSERT INTO copilot_iterations"
                        " (session_id, product_id, action_type, concept_text, predicted_score, metadata)"
                        " VALUES (?,?,?,?,?,?)",
                        (session_id, product_id, "initial_generate", hook_text,
                         sc["overall"], json.dumps(meta)),
                    )
                    iterations.append({
                        "id": cur.lastrowid, "session_id": session_id,
                        "action_type": "initial_generate", "concept_text": hook_text,
                        "predicted_score": sc["overall"], "is_favorite": 0,
                        "metadata": meta,
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
        hooks_rich  = action_result.get("hooks_rich", [])  # parallel list with archetype + D/C/PS
        is_rich     = action_type in ("ugc_concepts", "static_concepts", "script", "creator_brief")
        iterations: list[dict[str, Any]] = []

        _HOOK_ACTIONS = {"rewrite", "variants", "premium", "direct_response",
                         "curiosity", "mainstream", "adapt_audience"}

        with conn:
            for idx, item in enumerate(results):
                if action_type in _HOOK_ACTIONS:
                    # Plain hook string — score it
                    hook_text = item if isinstance(item, str) else str(item)
                    sc = score_concept(hook_text, conn)
                    predicted = sc["overall"]
                    # Attach D+C+PS archetype data if present
                    rich_obj = hooks_rich[idx] if idx < len(hooks_rich) else {}
                    if rich_obj.get("archetype"):
                        sc["archetype"] = rich_obj["archetype"]
                    if rich_obj.get("d"):
                        sc["dcp_d"] = rich_obj["d"]
                    if rich_obj.get("c"):
                        sc["dcp_c"] = rich_obj["c"]
                    if rich_obj.get("ps"):
                        sc["dcp_ps"] = rich_obj["ps"]
                    if rich_obj.get("clarity"):
                        sc["dcp_clarity"] = rich_obj["clarity"]
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

        # Also load production outputs for this session
        prod_rows = conn.execute(
            """SELECT po.id, po.output_type, po.concept_text, po.output_json,
                      po.is_approved, po.is_favorite, po.created_at
               FROM production_outputs po
               WHERE po.session_id = ?
               ORDER BY po.created_at ASC""",
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

        production_outputs = []
        for row in prod_rows:
            d = dict(row)
            try:
                d["output_data"] = json.loads(d.get("output_json") or "{}")
            except Exception:
                d["output_data"] = {}
            production_outputs.append(d)

        return jsonify({"session": dict(sess), "iterations": iterations,
                        "production_outputs": production_outputs})
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
      background_only      : bool?  — skip PIL compositing, AI image is final ad (default: True)
    }
    """
    from creative_intelligence.rendering.static_renderer import render_static_brief
    from creative_intelligence.rendering.schemas import VARIANT_STRATEGIES

    try:
        body   = request.get_json(force=True) or {}
        pid    = body.get("production_output_id")
        if not pid:
            return jsonify({"error": "production_output_id required"}), 400

        aspect          = body.get("aspect_ratio", "9:16")
        variants_req    = body.get("variants") or list(VARIANT_STRATEGIES)
        gen_images      = body.get("generate_images")   # None = use config
        model           = body.get("model") or None
        dry_run         = bool(body.get("dry_run", False))
        background_only = bool(body.get("background_only", True))

        conn   = _db()
        result = render_static_brief(
            production_output_id=int(pid),
            conn=conn,
            variants=tuple(v for v in variants_req if v in VARIANT_STRATEGIES),
            aspect_ratio=aspect,
            generate_images=gen_images,
            model=model,
            dry_run=dry_run,
            background_only=background_only,
        )
        conn.close()
        return jsonify(result)

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        app.logger.exception("render/generate failed")
        # Store the error message in render_outputs so it's visible without logs
        try:
            _ec = _db()
            _ec.execute(
                """UPDATE render_outputs SET error_message = ? WHERE id = (
                       SELECT MAX(id) FROM render_outputs WHERE provider_name IS NOT NULL
                   )""",
                (str(exc)[:500],),
            )
            _ec.commit(); _ec.close()
        except Exception:
            pass
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


# ─────────────────────────────────────────────
# Asset feedback (rejection notes + learning)
# ─────────────────────────────────────────────

@app.post("/api/render/asset/<int:asset_id>/feedback")
def render_asset_feedback(asset_id: int) -> Any:
    """Store rejection/improvement feedback for an asset, then re-score."""
    try:
        body          = request.get_json(force=True) or {}
        feedback_type = body.get("feedback_type", "rejection")
        tags          = body.get("rejection_tags", [])
        note          = body.get("note", "").strip()
        conn          = _db()

        # Look up product_id and variant_label for denormalisation
        row = conn.execute(
            """SELECT ra.variant_label, po.product_id
               FROM render_assets ra
               JOIN render_outputs ro ON ro.id = ra.render_output_id
               LEFT JOIN production_outputs po ON po.id = ro.source_production_output_id
               WHERE ra.id = ?""",
            (asset_id,),
        ).fetchone()
        product_id    = row["product_id"]    if row else None
        variant_label = row["variant_label"] if row else None

        conn.execute(
            """INSERT INTO asset_feedback
               (asset_id, asset_type, feedback_type, rejection_tags, note, product_id, variant_label)
               VALUES (?, 'render', ?, ?, ?, ?, ?)""",
            (asset_id, feedback_type, json.dumps(tags), note, product_id, variant_label),
        )
        conn.commit()

        # Re-score this asset and all assets for the same product (rates changed)
        from creative_intelligence.scoring import score_asset, score_product_assets
        score_asset(asset_id, conn)
        if product_id:
            score_product_assets(product_id, conn)

        conn.close()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/learnings")
def get_learnings() -> Any:
    """Return active creative learnings, optionally filtered by product_id."""
    try:
        product_id = request.args.get("product_id")
        conn = _db()
        if product_id:
            rows = conn.execute(
                """SELECT * FROM creative_learnings
                   WHERE active = 1 AND product_id = ?
                   ORDER BY id DESC""",
                (product_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT cl.*, p.name as product_name
                   FROM creative_learnings cl
                   LEFT JOIN products p ON p.id = cl.product_id
                   WHERE cl.active = 1
                   ORDER BY cl.id DESC"""
            ).fetchall()
        conn.close()
        return jsonify({"learnings": [dict(r) for r in rows]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/learnings/distill")
def distill_learnings() -> Any:
    """Distil recent feedback into creative_learnings rows via LLM.

    Body: { "product_id": str (optional) }
    Reads last 50 feedback entries, calls LLM to summarise patterns,
    writes new creative_learnings rows (deactivates old ones for same product first).
    """
    try:
        body       = request.get_json(force=True) or {}
        product_id = body.get("product_id")
        conn       = _db()

        # Fetch recent feedback
        if product_id:
            feedback_rows = conn.execute(
                """SELECT feedback_type, rejection_tags, note, variant_label, created_at
                   FROM asset_feedback
                   WHERE product_id = ?
                   ORDER BY id DESC LIMIT 50""",
                (product_id,),
            ).fetchall()
        else:
            feedback_rows = conn.execute(
                """SELECT feedback_type, rejection_tags, note, variant_label, product_id, created_at
                   FROM asset_feedback
                   ORDER BY id DESC LIMIT 50"""
            ).fetchall()

        if not feedback_rows:
            conn.close()
            return jsonify({"ok": True, "learnings": [], "message": "No feedback to distil"})

        # Build feedback summary for LLM
        feedback_lines = []
        for r in feedback_rows:
            tags = json.loads(r["rejection_tags"] or "[]")
            line = f"- [{r['feedback_type']}] variant={r['variant_label'] or 'unknown'}"
            if tags:
                line += f" tags=[{', '.join(tags)}]"
            if r["note"]:
                line += f' note="{r["note"]}"'
            feedback_lines.append(line)
        feedback_text = "\n".join(feedback_lines)

        from creative_intelligence.llm_client import get_llm_client
        client = get_llm_client()
        resp = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=800,
            system=(
                "You are a creative strategist analysing ad creative feedback to extract actionable learnings. "
                "Output ONLY valid JSON — no markdown, no explanation."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Analyse this creative feedback and extract 3–6 concise actionable learnings.\n\n"
                    f"Feedback:\n{feedback_text}\n\n"
                    f"Output JSON: {{\"learnings\": ["
                    f"{{\"learning_type\": \"avoid\" or \"prefer\", "
                    f"\"summary\": \"one clear sentence for prompt injection\", "
                    f"\"detail\": \"brief explanation\"}}]}}"
                ),
            }],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(raw)
        learnings = parsed.get("learnings", [])

        # Deactivate old learnings for this product, insert new ones
        if product_id:
            conn.execute(
                "UPDATE creative_learnings SET active = 0 WHERE product_id = ?",
                (product_id,),
            )
        else:
            conn.execute("UPDATE creative_learnings SET active = 0")

        inserted = []
        for lrn in learnings:
            summary = str(lrn.get("summary", "")).strip()
            if not summary:
                continue
            conn.execute(
                """INSERT INTO creative_learnings
                   (product_id, learning_type, source, summary, detail_json)
                   VALUES (?, ?, 'feedback', ?, ?)""",
                (product_id, lrn.get("learning_type", "avoid"),
                 summary, json.dumps({"detail": lrn.get("detail", "")})),
            )
            inserted.append(summary)
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "learnings": inserted})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/score/recompute")
def recompute_scores() -> Any:
    """Recompute quality_score for all render assets (or just one product).

    Body: { "product_id": str (optional) }
    """
    try:
        body       = request.get_json(force=True) or {}
        product_id = body.get("product_id")
        conn       = _db()

        from creative_intelligence.scoring import score_asset, score_product_assets
        if product_id:
            results = score_product_assets(product_id, conn)
        else:
            rows = conn.execute("SELECT id FROM render_assets").fetchall()
            results = [score_asset(r["id"], conn) for r in rows]

        conn.close()
        return jsonify({"ok": True, "scored": len(results)})
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

        # model_slug is the Replicate owner/name slug from the UI (e.g.
        # "google/nano-banana-pro"). get_provider expects "replicate" / provider
        # name, NOT a model slug — pass the slug as a kwarg to generate() instead.
        model_slug       = body.get("model") or None
        provider_name    = d.get("provider_name") or None
        aspect_ratio     = spec.get("aspect_ratio", "9:16")

        provider = get_provider(provider_name)
        gen_kwargs: dict = {}
        if model_slug:
            gen_kwargs["model"] = model_slug
        paths    = provider.generate(
            prompt=visual_prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
            **gen_kwargs,
        )

        if not paths:
            conn.close()
            return jsonify({"error": "provider returned no output"}), 500

        # AI image IS the final ad — save directly as final_ad, skip PIL compositor.
        # The compositor added mechanical duplicate text on top of AI-baked copy.
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
                "asset_role":       "final_ad",
            },
            asset_type="final_ad",
        )

        # Transfer collection flags (favorite / ready / approved) from the old
        # asset to the new one, then clear them on the old asset.
        # This keeps the new asset visible in the Collection after page reload.
        with conn:
            conn.execute(
                """UPDATE render_assets
                   SET is_favorite      = (SELECT is_favorite      FROM render_assets WHERE id = ?),
                       is_ready_to_test = (SELECT is_ready_to_test FROM render_assets WHERE id = ?),
                       review_status    = (SELECT review_status    FROM render_assets WHERE id = ?)
                   WHERE id = ?""",
                (asset_id, asset_id, asset_id, new_asset_id),
            )
            conn.execute(
                """UPDATE render_assets
                   SET is_favorite = 0, is_ready_to_test = 0, review_status = 'pending'
                   WHERE id = ?""",
                (asset_id,),
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

        # Image render assets
        image_rows = conn.execute(
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

        # Video storyboards
        video_rows = conn.execute(
            """SELECT
                vs.id as storyboard_id,
                vs.concept_text,
                vs.product_id,
                vs.total_duration_seconds,
                vs.source_output_type,
                vs.is_favorite,
                vs.is_approved,
                vs.created_at,
                p.name as product_name,
                COUNT(sc.id) as scene_count,
                SUM(CASE WHEN sc.render_asset_id IS NOT NULL THEN 1 ELSE 0 END) as frames_count
            FROM video_storyboards vs
            LEFT JOIN products p ON p.id = vs.product_id
            LEFT JOIN video_scenes sc ON sc.storyboard_id = vs.id
            WHERE vs.is_favorite = 1 OR vs.is_approved = 1
            GROUP BY vs.id
            ORDER BY vs.id DESC"""
        ).fetchall()

        # Assembled video outputs (favorited or approved)
        video_output_rows = conn.execute(
            """SELECT
                vo.id,
                vo.storyboard_id,
                vo.video_type,
                vo.output_path,
                vo.metadata_json,
                vo.status,
                vo.is_approved,
                vo.is_favorite,
                vo.is_ready_to_test,
                vo.created_at,
                vs.concept_text,
                vs.total_duration_seconds,
                vs.source_output_type,
                p.name as product_name
            FROM video_outputs vo
            JOIN video_storyboards vs ON vs.id = vo.storyboard_id
            LEFT JOIN products p ON p.id = vs.product_id
            WHERE vo.is_favorite = 1 OR vo.is_approved = 1
            ORDER BY vo.id DESC"""
        ).fetchall()

        conn.close()
        return jsonify({
            "images":        [dict(r) for r in image_rows],
            "videos":        [dict(r) for r in video_rows],
            "video_outputs": [dict(r) for r in video_output_rows],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Video storyboard
# ─────────────────────────────────────────────

@app.get("/video")
def video_page() -> Any:
    resp = make_response(render_template("video.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.get("/api/video/packages")
def video_available_packages() -> Any:
    """Return the best available upstream package for a concept/session.

    Query params:
      concept     str  — concept text (used for fuzzy match)
      session_id  str? — copilot session id (narrows search)

    Returns the most relevant script_package or ugc_brief if one exists,
    so the UI can show "Build from Script" / "Build from UGC Brief" labels.
    """
    try:
        concept    = request.args.get("concept", "").strip()
        session_id = request.args.get("session_id") or None
        if not concept:
            return jsonify({"package": None})

        conn = _db()
        # Look for Video Brief first (unified), then Script Package, then UGC Brief
        row = conn.execute(
            """SELECT id, output_type, concept_text, created_at
               FROM production_outputs
               WHERE output_type IN ('video_brief', 'script_package', 'ugc_brief')
                 AND (concept_text = ?
                      OR concept_text LIKE ?
                      OR (? IS NOT NULL AND session_id = ?))
               ORDER BY
                 CASE output_type
                   WHEN 'video_brief'    THEN 0
                   WHEN 'script_package' THEN 1
                   ELSE 2
                 END,
                 id DESC
               LIMIT 1""",
            (concept, f"%{concept[:40]}%", session_id, session_id),
        ).fetchone()
        conn.close()
        return jsonify({"package": dict(row) if row else None})
    except Exception as exc:
        return jsonify({"error": str(exc), "package": None}), 500


@app.post("/api/video/storyboard")
def video_storyboard_create() -> Any:
    """Generate a video storyboard from a concept or production package.

    Body:
      concept                      str  — the hook / concept text (required)
      product_id                   str? — product for context
      source_production_output_id  int? — explicit package to use as input
      source_output_type           str? — hint for the source type
      session_id                   str? — used for auto-package resolution
      video_type                   str? — ugc|farm_origin|product_hero|comparison_reveal
      aspect_ratio                 str? — default "9:16"
      generate_frames              bool?— generate scene images (default false)
      model                        str? — Replicate model slug override
      dry_run                      bool?— skip DB writes (default false)
    """
    from creative_intelligence.video.storyboard_builder import build_storyboard
    try:
        body           = request.get_json(force=True) or {}
        concept        = (body.get("concept") or "").strip()
        if not concept:
            return jsonify({"error": "concept is required"}), 400

        product_id     = body.get("product_id") or None
        source_id_raw  = body.get("source_production_output_id")
        source_id      = int(source_id_raw) if source_id_raw else None
        source_type    = body.get("source_output_type") or None
        session_id     = body.get("session_id") or None
        video_type     = body.get("video_type") or "ugc"
        aspect_ratio   = body.get("aspect_ratio", "9:16")
        generate_frames= bool(body.get("generate_frames", False))
        model          = body.get("model") or None
        dry_run        = bool(body.get("dry_run", False))

        conn   = _db()
        result = build_storyboard(
            concept                      = concept,
            product_id                   = product_id,
            conn                         = conn,
            source_production_output_id  = source_id,
            source_output_type           = source_type,
            session_id                   = session_id,
            video_type                   = video_type,
            aspect_ratio                 = aspect_ratio,
            generate_frames              = generate_frames,
            model                        = model,
            dry_run                      = dry_run,
        )
        conn.close()
        return jsonify(result)
    except Exception as exc:
        app.logger.exception("video/storyboard failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/storyboard/<int:storyboard_id>")
def video_storyboard_fetch(storyboard_id: int) -> Any:
    """Fetch a storyboard with its scenes and asset image paths."""
    try:
        conn = _db()
        sb = conn.execute(
            "SELECT * FROM video_storyboards WHERE id = ?", (storyboard_id,)
        ).fetchone()
        if not sb:
            conn.close()
            return jsonify({"error": "not found"}), 404

        scenes = conn.execute(
            """SELECT vs.*, ra.asset_path_or_url, ra.metadata_json
               FROM video_scenes vs
               LEFT JOIN render_assets ra ON ra.id = vs.render_asset_id
               WHERE vs.storyboard_id = ?
               ORDER BY vs.scene_index""",
            (storyboard_id,),
        ).fetchall()
        conn.close()

        result = dict(sb)
        result["scenes_detail"] = []
        for s in scenes:
            sd = dict(s)
            # Add image URL if asset exists
            if sd.get("render_asset_id"):
                sd["image_url"] = f"/api/render/asset/{sd['render_asset_id']}/image"
            result["scenes_detail"].append(sd)

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _build_video_visual_seed(scenes: list[dict]) -> str:
    """Build a concise style/character anchor for visual consistency across frames.

    Returns a SHORT style prefix (NOT a full scene description) to prepend to
    each scene's image generation prompt. The prefix describes WHO and STYLE only
    — no actions, no objects — so the model doesn't try to render two competing
    scenes in a single image.
    """
    if not scenes:
        return ""

    # Lighting: use lighting_style column (set by storyboard builder per scene)
    lighting = next(
        (s.get("lighting_style", "").strip() for s in scenes if s.get("lighting_style", "").strip()),
        "warm natural daylight",
    )

    # Background: infer from first scene's visual_description
    _BG_MAP = {
        "white background": "clean white background",
        "neutral background": "clean neutral background",
        "studio": "clean studio background",
        "kitchen": "kitchen setting",
        "bedroom": "bedroom setting",
        "living room": "living room setting",
        "outdoor": "outdoor natural setting",
        "garden": "outdoor garden setting",
    }
    background = "clean neutral background"
    for s in scenes:
        vd_lower = (s.get("visual_description") or "").lower()
        for kw, label in _BG_MAP.items():
            if kw in vd_lower:
                background = label
                break
        if background != "clean neutral background":
            break

    # Person type: infer from first person-facing scene (no action words)
    _PERSON_WORDS = ["talent", "creator", "woman", "man", "person", "girl", "guy", "model"]
    person_desc = ""
    for s in scenes:
        vd_lower = (s.get("visual_description") or "").lower()
        if any(w in vd_lower for w in _PERSON_WORDS):
            if "woman" in vd_lower or "girl" in vd_lower or "female" in vd_lower:
                person_desc = "young woman"
            elif "man" in vd_lower or "guy" in vd_lower or "male" in vd_lower:
                person_desc = "young man"
            else:
                person_desc = "creator on camera"
            break

    parts = []
    if person_desc:
        parts.append(person_desc)
    parts.append(background)
    parts.append(lighting)
    parts.append(
        "realistic UGC photo style, natural skin tones, consistent look throughout, "
        "single frame, single subject, natural proportions"
    )
    return ", ".join(parts) + ". "


@app.post("/api/video/storyboard/<int:storyboard_id>/generate-frames")
def video_generate_frames(storyboard_id: int) -> Any:
    """Generate scene frame images for an existing storyboard (parallel, visually consistent)."""
    from creative_intelligence.rendering.providers import get_provider
    from creative_intelligence.rendering.asset_store import save_asset
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    try:
        body  = request.get_json(force=True, silent=True) or {}
        model = body.get("model") or None
        aspect_ratio = body.get("aspect_ratio", "9:16")
        # Optional: override specific scene indices (default: all)
        scene_indices = body.get("scene_indices") or None  # list[int] or None = all

        conn = _db()
        scene_rows = conn.execute(
            "SELECT * FROM video_scenes WHERE storyboard_id = ? ORDER BY scene_index",
            (storyboard_id,),
        ).fetchall()
        if not scene_rows:
            conn.close()
            return jsonify({"error": "storyboard not found or has no scenes"}), 404

        scenes = [dict(s) for s in scene_rows]

        # Build visual consistency seed from first scene
        # This anchors person/setting/lighting across all generated frames
        visual_seed = _build_video_visual_seed(scenes)

        provider   = get_provider(None)
        gen_kwargs: dict = {}
        if model:
            gen_kwargs["model"] = model

        negative = (
            "text overlays, watermarks, blurry, low quality, distorted, "
            "generic stock photo, fake-looking, oversaturated, pixelated, "
            "collage, multiple photos in one image, split screen, storyboard panels, "
            "grid layout, picture-in-picture, montage, side by side, 4 images, "
            "5 images, multiple frames, comic strip, contact sheet, "
            "disproportionate scale, giant fruit, giant product, unrealistic size"
        )

        # Fixed seed across all scenes for consistent character/style
        # Derived from storyboard_id so it's stable on regeneration
        style_seed = (storyboard_id * 7919) % (2**31 - 1)

        # Determine which scenes need generation
        to_generate = []
        skipped = []
        for s in scenes:
            if scene_indices is not None and s["scene_index"] not in scene_indices:
                continue
            if s.get("render_asset_id"):
                skipped.append({"scene_index": s["scene_index"], "skipped": True,
                                 "render_asset_id": s["render_asset_id"]})
            else:
                to_generate.append(s)

        # Thread-safe DB connection factory (each thread gets its own connection)
        db_path = None
        try:
            from creative_intelligence.db import get_db_path
            db_path = get_db_path()
        except Exception:
            pass

        lock = threading.Lock()
        results = list(skipped)

        def gen_scene(s):
            from creative_intelligence.db import get_connection
            thread_conn = get_connection(db_path) if db_path else _db()
            try:
                vd = s.get("visual_description", "")
                lower_vd = vd.lower()
                # Prepend style anchor for person-facing shots; product-only shots
                # get a lighter prefix so we don't force a person into a macro shot
                _PERSON_WORDS = ["talent", "creator", "person", "face", "smile", "camera", "speaking", "holding"]
                _PRODUCT_ONLY = ["macro", "b-roll", "b roll", "product only", "no person"]
                is_person_shot = any(w in lower_vd for w in _PERSON_WORDS)
                is_product_only = any(w in lower_vd for w in _PRODUCT_ONLY) and not is_person_shot
                if visual_seed and is_person_shot:
                    prompt = visual_seed + vd
                elif visual_seed and not is_product_only:
                    # Light style prefix only (drop person description)
                    style_only = ", ".join(visual_seed.rstrip(". ").split(", ")[1:]) + ". " if ", " in visual_seed else ""
                    prompt = style_only + vd
                else:
                    prompt = vd

                scene_gen_kwargs = dict(gen_kwargs)
                scene_gen_kwargs["seed"] = style_seed  # same seed → consistent style/character

                paths = provider.generate(
                    prompt=prompt,
                    negative_prompt=negative,
                    aspect_ratio=aspect_ratio,
                    **scene_gen_kwargs,
                )
                if not paths:
                    return {"scene_index": s["scene_index"], "error": "no paths returned"}

                asset_id = save_asset(
                    render_output_id=0,
                    variant_label=f"video_scene_{s['scene_index']}",
                    source=paths[0],
                    conn=thread_conn,
                    metadata={
                        "storyboard_id": storyboard_id,
                        "scene_id":      s["scene_index"],
                        "purpose":       s.get("purpose", ""),
                        "aspect_ratio":  aspect_ratio,
                    },
                )
                with thread_conn:
                    thread_conn.execute(
                        "UPDATE video_scenes SET render_asset_id=? WHERE id=?",
                        (asset_id, s["id"]),
                    )
                return {"scene_index": s["scene_index"], "render_asset_id": asset_id,
                        "image_url": f"/api/render/asset/{asset_id}/image"}
            except Exception as fe:
                return {"scene_index": s["scene_index"], "error": str(fe)}
            finally:
                try:
                    thread_conn.close()
                except Exception:
                    pass

        # Generate all scenes in parallel (max 5 workers)
        with ThreadPoolExecutor(max_workers=min(5, len(to_generate) or 1)) as executor:
            futures = {executor.submit(gen_scene, s): s for s in to_generate}
            for future in as_completed(futures):
                with lock:
                    results.append(future.result())

        conn.close()
        return jsonify({"ok": True, "storyboard_id": storyboard_id, "frames": results})
    except Exception as exc:
        app.logger.exception("video/generate-frames failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/list")
def video_list() -> Any:
    """List all storyboards, newest first."""
    try:
        conn = _db()
        rows = conn.execute(
            """SELECT vs.id, vs.concept_text, vs.product_id, vs.total_duration_seconds,
                      vs.source_output_type, vs.video_type, vs.is_favorite, vs.is_approved,
                      vs.created_at, p.name as product_name,
                      COUNT(sc.id) as scene_count,
                      SUM(CASE WHEN sc.render_asset_id IS NOT NULL THEN 1 ELSE 0 END) as frames_count
               FROM video_storyboards vs
               LEFT JOIN products p ON p.id = vs.product_id
               LEFT JOIN video_scenes sc ON sc.storyboard_id = vs.id
               GROUP BY vs.id
               ORDER BY vs.id DESC"""
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/video/storyboard/<int:storyboard_id>/favorite")
def video_storyboard_favorite(storyboard_id: int) -> Any:
    """Toggle is_favorite on a video storyboard."""
    try:
        body = request.get_json(force=True) or {}
        fav  = bool(body.get("favorite", True))
        conn = _db()
        conn.execute(
            "UPDATE video_storyboards SET is_favorite = ? WHERE id = ?",
            (1 if fav else 0, storyboard_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "is_favorite": fav})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/video/storyboard/<int:storyboard_id>/approve")
def video_storyboard_approve(storyboard_id: int) -> Any:
    """Toggle is_approved on a video storyboard."""
    try:
        body     = request.get_json(force=True) or {}
        approved = bool(body.get("approved", True))
        conn     = _db()
        conn.execute(
            "UPDATE video_storyboards SET is_approved = ? WHERE id = ?",
            (1 if approved else 0, storyboard_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "is_approved": approved})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/video/storyboard/<int:storyboard_id>/scene/<int:scene_idx>/regenerate")
def video_scene_regenerate(storyboard_id: int, scene_idx: int) -> Any:
    """Regenerate the frame image for a single scene."""
    from creative_intelligence.rendering.providers import get_provider
    from creative_intelligence.rendering.asset_store import save_asset
    try:
        body         = request.get_json(force=True, silent=True) or {}
        model        = body.get("model") or None
        aspect_ratio = body.get("aspect_ratio", "9:16")
        prompt_override = body.get("visual_prompt") or None

        conn = _db()
        # Get ALL scenes for the storyboard (needed for visual seed)
        all_scenes = [dict(r) for r in conn.execute(
            "SELECT * FROM video_scenes WHERE storyboard_id = ? ORDER BY scene_index",
            (storyboard_id,),
        ).fetchall()]
        if not all_scenes:
            conn.close()
            return jsonify({"error": "storyboard not found"}), 404

        scene = next((s for s in all_scenes if s["scene_index"] == scene_idx), None)
        if not scene:
            conn.close()
            return jsonify({"error": f"scene {scene_idx} not found"}), 404

        # Build visual seed for consistency
        visual_seed = _build_video_visual_seed(all_scenes)
        style_seed = (storyboard_id * 7919) % (2**31 - 1)

        gen_kwargs: dict = {}
        if model:
            gen_kwargs["model"] = model
        gen_kwargs["seed"] = style_seed

        vd = prompt_override or scene.get("visual_description", "")
        lower_vd = vd.lower()
        _PERSON_WORDS = ["talent", "creator", "person", "face", "smile", "camera", "speaking", "holding"]
        _PRODUCT_ONLY = ["macro", "b-roll", "b roll", "product only"]
        is_person_shot  = any(w in lower_vd for w in _PERSON_WORDS)
        is_product_only = any(w in lower_vd for w in _PRODUCT_ONLY) and not is_person_shot
        if visual_seed and is_person_shot:
            prompt = visual_seed + vd
        elif visual_seed and not is_product_only:
            style_only = ", ".join(visual_seed.rstrip(". ").split(", ")[1:]) + ". " if ", " in visual_seed else ""
            prompt = style_only + vd
        else:
            prompt = vd

        negative = (
            "text overlays, watermarks, blurry, low quality, distorted, "
            "generic stock photo, fake-looking, oversaturated, pixelated, "
            "collage, multiple photos in one image, split screen, storyboard panels, "
            "grid layout, picture-in-picture, montage, side by side, 4 images, "
            "5 images, multiple frames, comic strip, contact sheet, "
            "disproportionate scale, giant fruit, giant product, unrealistic size"
        )

        provider = get_provider(None)
        paths = provider.generate(
            prompt=prompt,
            negative_prompt=negative,
            aspect_ratio=aspect_ratio,
            **gen_kwargs,
        )
        if not paths:
            conn.close()
            return jsonify({"error": "provider returned no images"}), 500

        asset_id = save_asset(
            render_output_id=0,
            variant_label=f"video_scene_{scene_idx}",
            source=paths[0],
            conn=conn,
            metadata={
                "storyboard_id": storyboard_id,
                "scene_id":      scene_idx,
                "purpose":       scene.get("purpose", ""),
                "aspect_ratio":  aspect_ratio,
            },
        )
        with conn:
            conn.execute(
                "UPDATE video_scenes SET render_asset_id=? WHERE storyboard_id=? AND scene_index=?",
                (asset_id, storyboard_id, scene_idx),
            )
        conn.close()
        return jsonify({
            "ok": True,
            "scene_index":    scene_idx,
            "render_asset_id": asset_id,
            "image_url":      f"/api/render/asset/{asset_id}/image",
        })
    except Exception as exc:
        app.logger.exception("video/scene-regenerate failed")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Video assembly routes  (v1.6)
# ─────────────────────────────────────────────

@app.post("/api/video/storyboard/<int:storyboard_id>/assemble")
def video_assemble(storyboard_id: int) -> Any:
    """Assemble an MP4 from existing scene frames for a storyboard.

    Body (all optional):
      video_type       str  — ugc|farm_origin|product_hero|comparison_reveal
      aspect_ratio     str  — default "9:16"
      add_text_overlays bool — draw scene text overlays (default true)
      dry_run          bool — skip file write and DB row (default false)
    """
    from creative_intelligence.video.assembler import assemble_video
    from creative_intelligence import config
    try:
        body = request.get_json(force=True, silent=True) or {}
        video_type        = body.get("video_type") or "ugc"
        aspect_ratio      = body.get("aspect_ratio", "9:16")
        add_text_overlays = bool(body.get("add_text_overlays", True))
        motion_mode       = body.get("motion_mode", "ken_burns")
        dry_run           = bool(body.get("dry_run", False))

        conn = _db()
        result = assemble_video(
            storyboard_id     = storyboard_id,
            conn              = conn,
            output_dir        = config.CI_VIDEO_OUTPUT_DIR,
            video_type        = video_type,
            aspect_ratio      = aspect_ratio,
            add_text_overlays = add_text_overlays,
            motion_mode       = motion_mode,
            dry_run           = dry_run,
        )
        conn.close()
        if result.get("output_id"):
            result["video_url"] = f"/api/video/output/{result['output_id']}/file"
        return jsonify(result)
    except Exception as exc:
        app.logger.exception("video/assemble failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/output/<int:output_id>")
def video_output_get(output_id: int) -> Any:
    """Fetch a video_outputs row with its storyboard info."""
    try:
        conn = _db()
        row = conn.execute(
            """SELECT vo.*, vs.concept_text, vs.source_output_type as sb_source_type
               FROM video_outputs vo
               LEFT JOIN video_storyboards vs ON vs.id = vo.storyboard_id
               WHERE vo.id = ?""",
            (output_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "video output not found"}), 404
        return jsonify(dict(row))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/output/<int:output_id>/file")
def video_output_file(output_id: int) -> Any:
    """Serve the assembled MP4 file."""
    import os
    try:
        conn = _db()
        row = conn.execute(
            "SELECT output_path FROM video_outputs WHERE id = ?", (output_id,)
        ).fetchone()
        conn.close()
        if not row or not row["output_path"]:
            return jsonify({"error": "video file not found"}), 404
        path = row["output_path"]
        if not os.path.exists(path):
            return jsonify({"error": "video file missing from disk"}), 404
        from flask import send_file
        return send_file(path, mimetype="video/mp4", as_attachment=False)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/storyboard/<int:storyboard_id>/outputs")
def video_storyboard_outputs(storyboard_id: int) -> Any:
    """List all assembled video outputs for a storyboard."""
    try:
        conn = _db()
        rows = conn.execute(
            """SELECT id, video_type, status, output_path, metadata_json,
                      is_approved, is_favorite, is_ready_to_test, created_at
               FROM video_outputs WHERE storyboard_id = ? ORDER BY id DESC""",
            (storyboard_id,),
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("output_path"):
                d["video_url"] = f"/api/video/output/{d['id']}/file"
            result.append(d)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/video/output/<int:output_id>/approve")
def video_output_approve(output_id: int) -> Any:
    """Toggle is_approved on a video output."""
    try:
        body     = request.get_json(force=True) or {}
        approved = bool(body.get("approved", True))
        conn = _db()
        with conn:
            conn.execute(
                "UPDATE video_outputs SET is_approved=? WHERE id=?",
                (1 if approved else 0, output_id),
            )
        row = conn.execute("SELECT is_approved FROM video_outputs WHERE id=?", (output_id,)).fetchone()
        conn.close()
        return jsonify({"ok": True, "is_approved": row["is_approved"] if row else approved})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/video/output/<int:output_id>/favorite")
def video_output_favorite(output_id: int) -> Any:
    """Toggle is_favorite on a video output."""
    try:
        body = request.get_json(force=True) or {}
        fav  = bool(body.get("favorite", True))
        conn = _db()
        with conn:
            conn.execute(
                "UPDATE video_outputs SET is_favorite=? WHERE id=?",
                (1 if fav else 0, output_id),
            )
        row = conn.execute("SELECT is_favorite FROM video_outputs WHERE id=?", (output_id,)).fetchone()
        conn.close()
        return jsonify({"ok": True, "is_favorite": row["is_favorite"] if row else fav})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/video/output/<int:output_id>/ready")
def video_output_ready(output_id: int) -> Any:
    """Toggle is_ready_to_test on a video output."""
    try:
        body  = request.get_json(force=True) or {}
        ready = bool(body.get("ready", True))
        conn  = _db()
        with conn:
            conn.execute(
                "UPDATE video_outputs SET is_ready_to_test=? WHERE id=?",
                (1 if ready else 0, output_id),
            )
        row = conn.execute("SELECT is_ready_to_test FROM video_outputs WHERE id=?", (output_id,)).fetchone()
        conn.close()
        return jsonify({"ok": True, "is_ready_to_test": row["is_ready_to_test"] if row else ready})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/types")
def video_types_list() -> Any:
    """Return all supported video types with labels."""
    from creative_intelligence.video.types import VIDEO_TYPE_LABELS, VIDEO_TYPE_CONFIGS
    return jsonify([
        {"value": k, "label": v,
         "scene_count_min": VIDEO_TYPE_CONFIGS[k].scene_count_min,
         "scene_count_max": VIDEO_TYPE_CONFIGS[k].scene_count_max,
         "pacing": VIDEO_TYPE_CONFIGS[k].pacing}
        for k, v in VIDEO_TYPE_LABELS.items()
    ])


# ─────────────────────────────────────────────
# Scene-level clip generation routes  (v1.7)
# ─────────────────────────────────────────────

@app.post("/api/video/storyboard/<int:storyboard_id>/generate-clips")
def video_generate_clips(storyboard_id: int) -> Any:
    """Start async clip generation for a storyboard. Returns immediately.

    Clips are generated in a background thread (each Replicate call takes
    30-60s). Poll GET /api/video/storyboard/<id>/clips to watch progress.

    Body (all optional):
      provider        str  — "mock" | "replicate" (default: CI_CLIP_PROVIDER)
      aspect_ratio    str  — default "9:16"
      skip_existing   bool — skip scenes with an existing ok clip (default true)
      dry_run         bool — prompt only, no API calls or DB writes (default false)
    """
    from creative_intelligence.video.clip_generator import generate_storyboard_clips
    try:
        body = request.get_json(force=True, silent=True) or {}
        provider_name = body.get("provider") or None
        aspect_ratio  = body.get("aspect_ratio", "9:16")
        skip_existing = bool(body.get("skip_existing", True))
        dry_run       = bool(body.get("dry_run", False))
        # Allow UI to override the clip model (e.g. minimax/video-01-live for image-to-video)
        clip_model    = body.get("clip_model") or None

        conn = _db()
        sb = conn.execute(
            "SELECT id, video_type, scenes_json FROM video_storyboards WHERE id = ?", (storyboard_id,)
        ).fetchone()
        if not sb:
            conn.close()
            return jsonify({"error": f"Storyboard {storyboard_id} not found"}), 404

        # Backfill video_scenes from scenes_json for storyboards created before
        # per-scene rows were introduced (old storyboards only have scenes_json).
        existing = conn.execute(
            "SELECT COUNT(*) as n FROM video_scenes WHERE storyboard_id = ?",
            (storyboard_id,),
        ).fetchone()["n"]
        if existing == 0 and sb["scenes_json"]:
            try:
                import json as _json
                raw_scenes = _json.loads(sb["scenes_json"]) or []
                for s in raw_scenes:
                    shot = s.get("shot") or {}
                    conn.execute(
                        """INSERT INTO video_scenes
                           (storyboard_id, scene_index, purpose, visual_description,
                            text_overlay, duration_seconds,
                            camera_type, framing, movement, product_focus, lighting_style)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            storyboard_id,
                            s.get("scene_id", 0),
                            s.get("purpose", ""),
                            s.get("visual_description", ""),
                            s.get("text_overlay", ""),
                            s.get("duration_seconds", 5),
                            shot.get("camera_type", "handheld"),
                            shot.get("framing", "medium"),
                            shot.get("movement", "none"),
                            shot.get("product_focus", ""),
                            shot.get("lighting_style", ""),
                        ),
                    )
                conn.commit()
            except Exception:
                app.logger.exception("Failed to backfill video_scenes from scenes_json")

        # Count scenes to return immediately
        scene_count = conn.execute(
            "SELECT COUNT(*) as n FROM video_scenes WHERE storyboard_id = ?",
            (storyboard_id,),
        ).fetchone()["n"]

        # When retrying (skip_existing=False), reset expired/failed clips so they
        # are treated as new and get fresh Replicate predictions.
        if not skip_existing:
            with conn:
                conn.execute(
                    """UPDATE video_scene_clips
                       SET status = 'pending', clip_path = NULL, clip_url = NULL,
                           prediction_id = NULL, error_message = NULL
                       WHERE storyboard_id = ? AND status IN ('expired', 'failed')""",
                    (storyboard_id,),
                )

        # Create all Replicate predictions synchronously — each takes ~1s, not 30-90s.
        # Predictions run on Replicate's infrastructure and survive container restarts.
        # UI polls /sync-clips to pick up results as they complete.
        result = generate_storyboard_clips(
            storyboard_id=storyboard_id,
            conn=conn,
            provider_name=provider_name,
            clip_model=clip_model,
            aspect_ratio=aspect_ratio,
            skip_existing=skip_existing,
            dry_run=dry_run,
        )
        conn.close()

        return jsonify({
            "status": "started",
            "storyboard_id": storyboard_id,
            "scene_count": scene_count,
            "pending": result.get("pending", 0),
            "message": f"Created {result.get('pending', 0)} predictions. Poll /sync-clips for results.",
        })
    except Exception as exc:
        app.logger.exception("video/generate-clips failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/storyboard/<int:storyboard_id>/clips")
def video_scene_clips_list(storyboard_id: int) -> Any:
    """Return all video_scene_clips rows for a storyboard."""
    try:
        conn = _db()
        sb = conn.execute(
            "SELECT id FROM video_storyboards WHERE id = ?", (storyboard_id,)
        ).fetchone()
        if not sb:
            conn.close()
            return jsonify({"error": "storyboard not found"}), 404

        from creative_intelligence.video.clip_generator import get_scene_clips
        clips = get_scene_clips(storyboard_id, conn)
        conn.close()
        return jsonify(clips)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/storyboard/<int:storyboard_id>/sync-clips")
def video_sync_clips(storyboard_id: int) -> Any:
    """Poll Replicate for pending predictions and update clip statuses.
    Called by the UI every 8s while clips are generating."""
    try:
        from creative_intelligence.video.clip_generator import poll_storyboard_clips, get_scene_clips
        conn = _db()
        poll_result = poll_storyboard_clips(storyboard_id, conn)
        clips = get_scene_clips(storyboard_id, conn)
        conn.close()
        return jsonify({**poll_result, "clips": clips})
    except Exception as exc:
        app.logger.exception("sync-clips failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/clip-file/<int:clip_id>")
def video_clip_file(clip_id: int) -> Any:
    """Serve a locally stored clip MP4 by clip row ID."""
    import os
    from flask import send_file
    try:
        conn = _db()
        row = conn.execute(
            "SELECT clip_local_path FROM video_scene_clips WHERE id = ?", (clip_id,)
        ).fetchone()
        conn.close()
        if not row or not row["clip_local_path"]:
            return jsonify({"error": "clip file not found"}), 404
        path = row["clip_local_path"]
        if not os.path.exists(path):
            return jsonify({"error": "clip file missing from disk"}), 404
        return send_file(path, mimetype="video/mp4", conditional=True)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Storage / maintenance
# ─────────────────────────────────────────────

@app.get("/api/admin/storage")
def admin_storage_info() -> Any:
    """Return disk usage and DB size. No auth required (read-only)."""
    import shutil as _shutil
    from creative_intelligence.rendering.asset_store import get_render_output_dir
    from creative_intelligence import config as _cfg
    try:
        from creative_intelligence.db import get_db_path as _get_db_path
        # Always report the volume-backed data dir (not /tmp)
        _volume_dir = _get_db_path().parent
        _volume_dir.mkdir(parents=True, exist_ok=True)
        total_b, used_b, free_b = _shutil.disk_usage(str(_volume_dir))
        # Count image files in both volume render dir and current render dir
        render_dir = get_render_output_dir()
        _volume_render = _volume_dir / "render_outputs"
        scan_dirs = {render_dir, _volume_render}
        img_files = [
            f for d in scan_dirs if d.exists()
            for f in d.rglob("*") if f.is_file()
        ]
        img_bytes = sum(f.stat().st_size for f in img_files)
        # DB size
        db_path = _get_db_path()
        db_bytes = db_path.stat().st_size if db_path.exists() else 0
        return jsonify({
            "disk_total_mb":  round(total_b / 1_048_576),
            "disk_used_mb":   round(used_b  / 1_048_576),
            "disk_free_mb":   round(free_b  / 1_048_576),
            "disk_used_pct":  round(used_b / total_b * 100, 1),
            "images_count":   len(img_files),
            "images_mb":      round(img_bytes / 1_048_576, 1),
            "db_mb":          round(db_bytes  / 1_048_576, 1),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/admin/cleanup")
def admin_cleanup() -> Any:
    """
    Free up disk space while keeping every protected image.

    SAFE TO DELETE — image files for render_outputs where:
      - No asset has is_favorite=1, review_status='approved', or is_ready_to_test=1
      - AND the render_output was created more than `keep_days` days ago (default 7)
      - OR the file is corrupt (< 2 KB)

    NEVER DELETED — any file whose asset_id is protected (fav / approved / ready).
    DB rows are never deleted; only files on disk.

    Body (all optional): { keep_days: int (default 7) }
    """
    import shutil as _shutil
    from creative_intelligence.rendering.asset_store import get_render_output_dir
    from creative_intelligence import config as _cfg
    try:
        body      = request.get_json(force=True, silent=True) or {}
        keep_days = int(body.get("keep_days", 7))
        conn      = _db()

        # 1. Find all asset file paths that ARE protected
        protected_paths = set()
        prot_rows = conn.execute(
            """SELECT ra.asset_path_or_url
               FROM render_assets ra
               WHERE ra.is_favorite = 1
                  OR ra.review_status = 'approved'
                  OR ra.is_ready_to_test = 1"""
        ).fetchall()
        for r in prot_rows:
            p = r["asset_path_or_url"] or ""
            if p and not p.startswith("http") and not p.startswith("mock://"):
                protected_paths.add(str(Path(p).resolve()))

        # 2. Collect ALL image files on disk — scan current + volume render path
        render_dir = get_render_output_dir()
        from creative_intelligence.db import get_db_path as _get_db_path
        # Always scan the volume-backed path regardless of CI_RENDER_OUTPUT_DIR
        _volume_render = _get_db_path().parent / "render_outputs"
        scan_dirs = {render_dir, _volume_render}
        all_files = [
            f for d in scan_dirs if d.exists()
            for f in d.rglob("*") if f.is_file()
        ]

        deleted_files, deleted_bytes, kept_files = [], 0, 0
        corrupt_deleted = 0

        import time as _time
        cutoff = _time.time() - keep_days * 86_400

        for f in all_files:
            resolved = str(f.resolve())
            fsize    = f.stat().st_size

            # Always keep protected files
            if resolved in protected_paths:
                kept_files += 1
                continue

            # Delete corrupt files (< 2 KB — HTML error pages etc.) immediately
            if fsize < 2048:
                f.unlink(missing_ok=True)
                corrupt_deleted += 1
                deleted_bytes += fsize
                deleted_files.append(str(f))
                continue

            # Delete unprotected files older than keep_days
            if f.stat().st_mtime < cutoff:
                deleted_bytes += fsize
                f.unlink(missing_ok=True)
                deleted_files.append(str(f))
            else:
                kept_files += 1

        # 3. Remove empty directories
        for d in sorted(render_dir.rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()   # only succeeds if empty
                except OSError:
                    pass

        # 4. VACUUM the SQLite database to reclaim space from deleted rows
        db_path = Path(_cfg.CI_DB_PATH) if _cfg.CI_DB_PATH else None
        db_before = db_path.stat().st_size if db_path and db_path.exists() else 0
        try:
            conn.execute("VACUUM")
            conn.commit()
        except Exception:
            pass
        db_after = db_path.stat().st_size if db_path and db_path.exists() else 0
        conn.close()

        total_b, used_b, free_b = _shutil.disk_usage(str(render_dir))
        return jsonify({
            "ok":               True,
            "files_deleted":    len(deleted_files),
            "corrupt_deleted":  corrupt_deleted,
            "files_kept":       kept_files,
            "freed_mb":         round(deleted_bytes / 1_048_576, 1),
            "db_vacuumed_mb":   round((db_before - db_after) / 1_048_576, 1),
            "disk_free_mb_now": round(free_b / 1_048_576),
            "disk_used_pct_now":round(used_b / total_b * 100, 1),
            "keep_days":        keep_days,
            "protected_count":  len(protected_paths),
        })
    except Exception as exc:
        app.logger.exception("cleanup failed")
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────

def _startup_cleanup() -> None:
    """Delete non-protected render images older than 1 day on startup.

    Keeps the Railway volume from filling up between deploys without
    losing any favorited / approved / ready-to-test assets.
    """
    try:
        import shutil as _sh, time as _t
        from creative_intelligence.rendering.asset_store import get_render_output_dir
        from creative_intelligence.db import get_connection, init_db, get_db_path

        init_db()
        conn = get_connection()
        protected = set()
        for r in conn.execute(
            """SELECT asset_path_or_url FROM render_assets
               WHERE is_favorite=1 OR review_status='approved' OR is_ready_to_test=1"""
        ).fetchall():
            p = r["asset_path_or_url"] or ""
            if p and not p.startswith(("http", "mock://")):
                protected.add(str(Path(p).resolve()))
        conn.close()

        cutoff = _t.time() - 86_400  # 1 day
        volume_render = get_db_path().parent / "render_outputs"
        for d in {get_render_output_dir(), volume_render}:
            if not d.exists():
                continue
            for f in d.rglob("*"):
                if not f.is_file():
                    continue
                if str(f.resolve()) in protected:
                    continue
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
    except Exception:
        pass  # Never block startup


# ─────────────────────────────────────────────
# Admin: disk cleanup
# ─────────────────────────────────────────────

@app.post("/api/admin/cleanup-disk")
def admin_cleanup_disk() -> Any:
    """
    Free disk space on the Railway volume:
    - Delete clip files for storyboards older than 7 days
    - Delete render output files (background images) older than 7 days,
      keeping files for approved / favorited / ready-to-test assets
    - VACUUM the SQLite database to reclaim pages
    Returns a summary of bytes freed.
    """
    import shutil
    import time as _time
    from creative_intelligence import config

    freed_bytes  = 0
    deleted_files = 0
    errors: list[str] = []

    cutoff = _time.time() - 7 * 86400  # 7 days ago

    # ── 1. Clip files ──────────────────────────────────────────────────────
    clip_dir = Path(config.CI_CLIP_OUTPUT_DIR)
    if clip_dir.exists():
        for f in clip_dir.rglob("*.mp4"):
            try:
                if f.stat().st_mtime < cutoff:
                    sz = f.stat().st_size
                    f.unlink()
                    freed_bytes  += sz
                    deleted_files += 1
            except Exception as e:
                errors.append(str(e))

    # ── 2. Render output files (background images only — keep final ads) ───
    render_dir = Path(config.CI_RENDER_OUTPUT_DIR)
    conn = _db()
    try:
        # Collect paths we must keep: approved / fav / ready assets
        keep_rows = conn.execute(
            """SELECT asset_path_or_url FROM render_assets
               WHERE is_favorite=1 OR is_ready_to_test=1
                  OR review_status='approved'"""
        ).fetchall()
        keep_paths = {r["asset_path_or_url"] for r in keep_rows if r["asset_path_or_url"]}
    except Exception as e:
        keep_paths = set()
        errors.append(f"keep-paths query: {e}")
    finally:
        conn.close()

    if render_dir.exists():
        for f in render_dir.rglob("*"):
            if not f.is_file():
                continue
            if str(f) in keep_paths:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    sz = f.stat().st_size
                    f.unlink()
                    freed_bytes  += sz
                    deleted_files += 1
            except Exception as e:
                errors.append(str(e))
        # Remove empty subdirectories
        for d in sorted(render_dir.rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()  # only removes if empty
                except Exception:
                    pass

    # ── 3. VACUUM SQLite ───────────────────────────────────────────────────
    try:
        vconn = _db()
        vconn.execute("VACUUM")
        vconn.close()
        vacuum_ok = True
    except Exception as e:
        vacuum_ok = False
        errors.append(f"VACUUM: {e}")

    # ── 4. Report disk usage ───────────────────────────────────────────────
    try:
        usage = shutil.disk_usage("/app")
        disk_info = {
            "total_gb":  round(usage.total / 1e9, 2),
            "used_gb":   round(usage.used  / 1e9, 2),
            "free_gb":   round(usage.free  / 1e9, 2),
        }
    except Exception:
        disk_info = {}

    return jsonify({
        "ok":            True,
        "freed_mb":      round(freed_bytes / 1e6, 2),
        "deleted_files": deleted_files,
        "vacuum":        vacuum_ok,
        "disk":          disk_info,
        "errors":        errors,
    })


@app.get("/api/admin/disk-usage")
def admin_disk_usage() -> Any:
    """Quick check of volume disk usage."""
    import shutil
    from creative_intelligence import config

    result: dict = {}
    try:
        usage = shutil.disk_usage("/app")
        result["disk"] = {
            "total_gb": round(usage.total / 1e9, 2),
            "used_gb":  round(usage.used  / 1e9, 2),
            "free_gb":  round(usage.free  / 1e9, 2),
            "pct_used": round(usage.used / usage.total * 100, 1),
        }
    except Exception as e:
        result["disk_error"] = str(e)

    for label, dir_path in [
        ("clips",   config.CI_CLIP_OUTPUT_DIR),
        ("renders", config.CI_RENDER_OUTPUT_DIR),
    ]:
        try:
            p = Path(dir_path)
            if p.exists():
                files = list(p.rglob("*"))
                sz = sum(f.stat().st_size for f in files if f.is_file())
                result[label] = {"files": len([f for f in files if f.is_file()]),
                                  "mb": round(sz / 1e6, 2)}
            else:
                result[label] = {"files": 0, "mb": 0}
        except Exception as e:
            result[label] = {"error": str(e)}

    return jsonify(result)


def _startup_backfill_product_images() -> None:
    """On startup, fetch Shopify product images for any products missing image_url.

    Runs in a background thread so it never delays app startup.
    Only fires if CI_SHOPIFY_ENABLED=1 and there are products without image_url.
    """
    import threading

    def _run() -> None:
        try:
            from creative_intelligence import config
            if not config.CI_SHOPIFY_ENABLED:
                return
            from creative_intelligence.db import get_connection, init_db
            init_db()
            conn = get_connection()
            # Check how many products are missing image_url
            missing = conn.execute(
                """SELECT COUNT(*) as n FROM products
                   WHERE active=1 AND (image_url IS NULL OR image_url='')
                     AND shopify_product_id IS NOT NULL"""
            ).fetchone()["n"]
            if missing == 0:
                conn.close()
                return
            import logging
            _log = logging.getLogger(__name__)
            _log.info("Backfilling image_url for %d products from Shopify…", missing)
            from creative_intelligence.product_knowledge.shopify_adapter import (
                fetch_shopify_products, map_shopify_product
            )
            raw_products = fetch_shopify_products()
            updated = 0
            with conn:
                for raw in raw_products:
                    img_list = raw.get("images") or []
                    if not img_list:
                        continue
                    first = img_list[0]
                    img_url = first.get("src") or first.get("url") or None
                    if not img_url:
                        continue
                    db_id = f"shopify-{raw['id']}"
                    conn.execute(
                        "UPDATE products SET image_url=? WHERE id=? AND (image_url IS NULL OR image_url='')",
                        (img_url, db_id),
                    )
                    updated += 1
            conn.close()
            _log.info("Backfilled image_url for %d products.", updated)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Product image backfill failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


# ─────────────────────────────────────────────
# CANDLE LABEL STUDIO
# GHF "1d · Full-bleed Photo" label artwork. Deterministic recipe grid —
# no LLM in the loop. Isolated from the ads render pipeline above.
# ─────────────────────────────────────────────

@app.get("/labels")
def labels_page() -> Any:
    """Candle label studio: pick a recipe, build the prompt, generate."""
    return render_template("labels.html")


@app.get("/api/labels/config")
def labels_config() -> Any:
    """Full option grid for the pickers (SKUs + B1/B2/B3/B4/B5)."""
    from creative_intelligence.labels.builder import ui_config
    return jsonify(ui_config())


def _label_codes(data: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        (data.get("sku") or "").strip().upper(),
        (data.get("s") or "").strip().upper(),
        (data.get("i") or "").strip().upper(),
        (data.get("e") or "").strip().upper(),
        (data.get("h") or "").strip().upper(),
        (data.get("p") or "").strip().upper(),
    )


@app.post("/api/labels/prompt")
def labels_prompt() -> Any:
    """Assemble the prompt for a recipe. No generation, no cost, no DB write."""
    from creative_intelligence.labels.builder import assemble_prompt, RecipeError

    data = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(assemble_prompt(*_label_codes(data)))
    except RecipeError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("labels/prompt failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/api/labels/generate")
def labels_generate() -> Any:
    """Assemble the prompt, generate the image, persist it. Costs real money."""
    from creative_intelligence import config
    from creative_intelligence.labels.builder import assemble_prompt, RecipeError
    from creative_intelligence.labels.store import save_label_image
    from creative_intelligence.rendering.providers import get_provider

    from creative_intelligence.labels import recipes as _R

    data = request.get_json(force=True, silent=True) or {}
    sku, s, i, e, h, p = _label_codes(data)
    # Default to the label studio's own model, NOT config.CI_REPLICATE_MODEL —
    # the config default (SD 3.5 Turbo) currently fails on this account.
    model = (data.get("model") or "").strip() or _R.DEFAULT_MODEL

    try:
        built = assemble_prompt(sku, s, i, e, h, p)
    except RecipeError as exc:
        return jsonify({"error": str(exc)}), 400

    conn = _db()
    with conn:
        cur = conn.execute(
            """INSERT INTO label_renders
                 (sku, recipe, code_s, code_i, code_e, code_h, code_p,
                  prompt, negative_prompt, provider, model, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'generating')""",
            (sku, built["recipe"], s, i, e, h, p,
             built["prompt"], built["negative_prompt"],
             config.CI_IMAGE_PROVIDER, model),
        )
    row_id = cur.lastrowid

    try:
        provider = get_provider()
        kwargs: dict[str, Any] = {"model": model} if model else {}
        paths = provider.generate(
            prompt=built["prompt"],
            negative_prompt=built["negative_prompt"],
            aspect_ratio="2:3",
            **kwargs,
        )
        if not paths:
            raise RuntimeError("Image provider returned no images")

        saved = save_label_image(paths[0], sku, built["recipe"])
        with conn:
            conn.execute(
                "UPDATE label_renders SET image_path=?, status='done' WHERE id=?",
                (saved, row_id),
            )
        conn.close()
        return jsonify({**built, "id": row_id, "status": "done"})

    except Exception as exc:
        app.logger.exception("labels/generate failed")
        with conn:
            conn.execute(
                "UPDATE label_renders SET status='failed', error_message=? WHERE id=?",
                (str(exc)[:500], row_id),
            )
        conn.close()
        return jsonify({**built, "id": row_id, "status": "failed",
                        "error": str(exc)}), 500


@app.get("/api/labels/history")
def labels_history() -> Any:
    """Recent label renders, newest first. Optional ?sku= filter."""
    sku = (request.args.get("sku") or "").strip().upper()
    limit = max(1, min(int(request.args.get("limit", 40)), 200))

    conn = _db()
    sql = ("SELECT id, sku, recipe, status, is_favorite, notes, created_at, "
           "error_message, image_path IS NOT NULL AS has_image "
           "FROM label_renders")
    params: list[Any] = []
    if sku:
        sql += " WHERE sku = ?"
        params.append(sku)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return jsonify({"renders": rows})


@app.get("/api/labels/<int:render_id>")
def labels_detail(render_id: int) -> Any:
    """Full stored record for one render, including the exact prompt used."""
    conn = _db()
    row = conn.execute(
        "SELECT * FROM label_renders WHERE id = ?", (render_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.post("/api/labels/<int:render_id>/favorite")
def labels_favorite(render_id: int) -> Any:
    conn = _db()
    row = conn.execute(
        "SELECT is_favorite FROM label_renders WHERE id = ?", (render_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "not found"}), 404
    new_val = 0 if row["is_favorite"] else 1
    with conn:
        conn.execute(
            "UPDATE label_renders SET is_favorite=? WHERE id=?", (new_val, render_id)
        )
    conn.close()
    return jsonify({"id": render_id, "is_favorite": new_val})


@app.get("/api/labels/image/<int:render_id>")
def labels_image(render_id: int) -> Any:
    """Serve a generated label image by render id."""
    conn = _db()
    row = conn.execute(
        "SELECT image_path FROM label_renders WHERE id = ?", (render_id,)
    ).fetchone()
    conn.close()
    if row is None or not row["image_path"]:
        return ("", 404)

    path = Path(row["image_path"])
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent.parent.parent / path)
    if not path.exists():
        app.logger.warning("labels_image %s: missing file %s", render_id, path)
        return ("", 404)
    return send_file(str(path), mimetype="image/png", conditional=True)


# ─────────────────────────────────────────────
# AD STUDIO
# Reference-photo product ads → Nano Banana Pro (image_input). Isolated module.
# ─────────────────────────────────────────────

@app.get("/adstudio")
def adstudio_page() -> Any:
    return render_template("adstudio.html")


@app.get("/core")
def core_page() -> Any:
    return render_template("core.html")


@app.get("/api/adstudio/config")
def adstudio_config() -> Any:
    from creative_intelligence.adstudio.formats import format_list, ASPECT_RATIOS, DEFAULT_ASPECT
    from creative_intelligence.adstudio.profiles import PROFILES
    types = {k: {"label": v["label"], "notes_label": v["notes_label"],
                 "archetypes": [x["name"] for x in v["formats"].values()]}
             for k, v in PROFILES.items()}
    from creative_intelligence.adstudio import textads
    return jsonify({"formats": format_list(), "aspect_ratios": ASPECT_RATIOS,
                    "default_aspect": DEFAULT_ASPECT, "product_types": types,
                    "ad_variants": textads.variant_list(),
                    "ad_default_aspect": textads.DEFAULT_ASPECT})


# ── Products ─────────────────────────────────────────────────────────────
@app.post("/api/adstudio/products")
def adstudio_create_product() -> Any:
    from creative_intelligence.adstudio.profiles import normalize_type
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    conn = _db()
    with conn:
        cur = conn.execute(
            "INSERT INTO adstudio_products (name, description, scent_notes, physical_desc, notes,"
            " varieties, product_type) VALUES (?,?,?,?,?,?,?)",
            (name, d.get("description", ""), d.get("scent_notes", ""),
             d.get("physical_desc", ""), d.get("notes", ""), d.get("varieties", ""),
             normalize_type(d.get("product_type"))),
        )
    pid = cur.lastrowid
    conn.close()
    return jsonify({"id": pid, "name": name})


@app.post("/api/adstudio/product/<int:pid>/update")
def adstudio_update_product(pid: int) -> Any:
    """Update editable product fields (name, scent_notes, physical_desc, description, notes, …)."""
    from creative_intelligence.adstudio.profiles import normalize_type
    d = request.get_json(force=True, silent=True) or {}
    if "product_type" in d:
        d["product_type"] = normalize_type(d["product_type"])
    sets, vals = [], []
    for k in ("name", "scent_notes", "physical_desc", "description", "notes",
              "varieties", "shopify_url", "info_json", "product_type"):
        if k in d:
            sets.append(f"{k}=?"); vals.append(d[k])
    if not sets:
        return jsonify({"error": "no fields"}), 400
    vals.append(pid)
    conn = _db()
    with conn:
        conn.execute(f"UPDATE adstudio_products SET {', '.join(sets)} WHERE id=?", vals)
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/adstudio/products")
def adstudio_list_products() -> Any:
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM adstudio_photos WHERE product_id=p.id) AS photo_count,"
        " (SELECT COUNT(*) FROM adstudio_hooks WHERE product_id=p.id) AS hook_count"
        " FROM adstudio_products p ORDER BY p.id DESC").fetchall()]
    conn.close()
    return jsonify({"products": rows})


@app.get("/api/adstudio/product/<int:pid>")
def adstudio_get_product(pid: int) -> Any:
    conn = _db()
    prod = conn.execute("SELECT * FROM adstudio_products WHERE id=?", (pid,)).fetchone()
    if prod is None:
        conn.close()
        return jsonify({"error": "not found"}), 404
    photos = [dict(r) for r in conn.execute(
        "SELECT id, is_primary, role FROM adstudio_photos WHERE product_id=? ORDER BY id", (pid,)).fetchall()]
    hooks = [dict(r) for r in conn.execute(
        "SELECT * FROM adstudio_hooks WHERE product_id=? ORDER BY id DESC", (pid,)).fetchall()]
    conn.close()
    return jsonify({"product": dict(prod), "photos": photos, "hooks": hooks})


# ── Photos ───────────────────────────────────────────────────────────────
@app.post("/api/adstudio/product/<int:pid>/photo")
def adstudio_upload_photo(pid: int) -> Any:
    from creative_intelligence.adstudio.store import save_reference_photo
    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "no file"}), 400
    raw = f.read()
    if not raw:
        return jsonify({"error": "empty file"}), 400
    path = save_reference_photo(raw, f.filename or "photo.png", pid)
    conn = _db()
    is_primary = 0 if conn.execute(
        "SELECT 1 FROM adstudio_photos WHERE product_id=? LIMIT 1", (pid,)).fetchone() else 1
    with conn:
        cur = conn.execute(
            "INSERT INTO adstudio_photos (product_id, path, is_primary) VALUES (?,?,?)",
            (pid, path, is_primary))
    photo_id = cur.lastrowid
    conn.close()
    return jsonify({"id": photo_id, "is_primary": is_primary})


@app.get("/api/adstudio/photo/<int:photo_id>")
def adstudio_serve_photo(photo_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT path FROM adstudio_photos WHERE id=?", (photo_id,)).fetchone()
    conn.close()
    if row is None or not Path(row["path"]).exists():
        return ("", 404)
    return send_file(row["path"], conditional=True)


@app.post("/api/adstudio/photo/<int:photo_id>/delete")
def adstudio_delete_photo(photo_id: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_photos WHERE id=?", (photo_id,))
    conn.close()
    return jsonify({"ok": True})


# ── Hooks ────────────────────────────────────────────────────────────────
_HOOK_FIELDS = ("hook_text", "archetype", "d", "c", "ps",
                "headline", "subhead", "body", "cta")


@app.post("/api/adstudio/product/<int:pid>/hook")
def adstudio_add_hook(pid: int) -> Any:
    d = request.get_json(force=True, silent=True) or {}
    vals = [d.get(k, "") for k in _HOOK_FIELDS]
    if not (vals[0] or vals[5]):  # need hook_text or headline
        return jsonify({"error": "hook_text or headline required"}), 400
    conn = _db()
    with conn:
        cur = conn.execute(
            f"INSERT INTO adstudio_hooks (product_id, {', '.join(_HOOK_FIELDS)}, source, pattern_id)"
            f" VALUES (?{',?'*len(_HOOK_FIELDS)},?,?)",
            (pid, *vals, d.get("source", "manual"), d.get("pattern_id") or None))
    hid = cur.lastrowid
    conn.close()
    return jsonify({"id": hid})


@app.post("/api/adstudio/hook/<int:hid>/delete")
def adstudio_delete_hook(hid: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_hooks WHERE id=?", (hid,))
    conn.close()
    return jsonify({"ok": True})


# ── Claude: reference-aware chat + structured hook suggestions ────────────
def _product_dict(conn, pid: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM adstudio_products WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def _primary_photo_uris(conn, pid: int, limit: int = 3) -> list[str]:
    from creative_intelligence.adstudio.store import to_data_uri
    rows = conn.execute(
        "SELECT path FROM adstudio_photos WHERE product_id=? ORDER BY is_primary DESC, id"
        " LIMIT ?", (pid, limit)).fetchall()
    uris = []
    for r in rows:
        try:
            uris.append(to_data_uri(r["path"]))
        except Exception:
            pass
    return uris


@app.post("/api/adstudio/product/<int:pid>/chat")
def adstudio_chat(pid: int) -> Any:
    from creative_intelligence.adstudio.chat import chat_reply
    d = request.get_json(force=True, silent=True) or {}
    messages = d.get("messages") or []
    if not messages:
        return jsonify({"error": "messages required"}), 400
    conn = _db()
    prod = _product_dict(conn, pid)
    uris = _primary_photo_uris(conn, pid)
    conn.close()
    if prod is None:
        return jsonify({"error": "product not found"}), 404
    try:
        reply = chat_reply(messages, prod, uris)
        return jsonify({"reply": reply})
    except Exception as exc:
        app.logger.exception("adstudio chat failed")
        return jsonify({"error": str(exc)[:300]}), 500


@app.post("/api/adstudio/product/<int:pid>/extract-hooks")
def adstudio_extract_hooks(pid: int) -> Any:
    """Extract hooks from a chat conversation and (optionally) save them."""
    from creative_intelligence.adstudio.chat import extract_hooks
    d = request.get_json(force=True, silent=True) or {}
    messages = d.get("messages") or []
    if not messages:
        return jsonify({"error": "no conversation to pull from"}), 400
    conn = _db()
    prod = _product_dict(conn, pid)
    uris = _primary_photo_uris(conn, pid)
    conn.close()
    if prod is None:
        return jsonify({"error": "product not found"}), 404
    try:
        hooks = extract_hooks(prod, messages, uris)
    except Exception as exc:
        app.logger.exception("extract-hooks failed")
        return jsonify({"error": str(exc)[:300]}), 500

    saved = 0
    if d.get("save") and hooks:
        conn = _db()
        with conn:
            for h in hooks:
                conn.execute(
                    "INSERT INTO adstudio_hooks (product_id, hook_text, archetype, d, c, ps,"
                    " headline, subhead, body, cta, source) VALUES (?,?,?,?,?,?,?,?,?,?, 'chat')",
                    (pid, h.get("hook_text", ""), h.get("archetype", ""), h.get("d", ""),
                     h.get("c", ""), h.get("ps", ""), h.get("headline", ""),
                     h.get("subhead", ""), h.get("body", ""), h.get("cta", "")))
                saved += 1
        conn.close()
    return jsonify({"hooks": hooks, "saved_count": saved})


@app.post("/api/adstudio/product/<int:pid>/suggest-hooks")
def adstudio_suggest_hooks(pid: int) -> Any:
    """
    Body: {n, pattern: "auto" | "none" | <pattern id>, idea?, brief?}.
    "auto" builds on the top winning patterns from our Meta data.
    """
    from creative_intelligence.adstudio.chat import suggest_hooks
    from creative_intelligence.adstudio.winning import current_patterns, get_pattern
    d = request.get_json(force=True, silent=True) or {}
    mode = str(d.get("pattern", "auto"))
    idea = (d.get("idea") or "").strip()
    conn = _db()
    prod = _product_dict(conn, pid)
    uris = _primary_photo_uris(conn, pid)
    if mode == "none":
        patterns = []
    elif mode.isdigit():
        one = get_pattern(conn, int(mode))
        patterns = [one] if one else []
    else:
        patterns = current_patterns(conn, limit=4)
    conn.close()
    if prod is None:
        return jsonify({"error": "product not found"}), 404
    if not patterns and not idea and mode != "none":
        return jsonify({"error": "No winning patterns yet — sync from Meta, or type an idea to test."}), 400
    try:
        hooks = suggest_hooks(prod, int(d.get("n", 4)), d.get("brief", ""), uris,
                              patterns=patterns, idea=idea)
        labels = {p["id"]: p["label"] for p in patterns}
        for h in hooks:
            h["pattern_label"] = labels.get(h.get("pattern_id"), "")
            if h["pattern_label"]:
                h["archetype"] = h["pattern_label"]
            h["source"] = ("pattern+idea" if h["pattern_label"] and idea else
                           "pattern" if h["pattern_label"] else "idea" if idea else "claude")
        return jsonify({"hooks": hooks})
    except Exception as exc:
        app.logger.exception("adstudio suggest-hooks failed")
        return jsonify({"error": str(exc)[:300]}), 500


# ── Winning patterns (real Meta results) + Meta sync ───────────────────────
@app.get("/api/adstudio/patterns")
def adstudio_patterns() -> Any:
    from creative_intelligence.adstudio.winning import current_patterns, data_status
    conn = _db()
    out = {"patterns": current_patterns(conn, limit=12), "status": data_status(conn)}
    conn.close()
    return jsonify(out)


@app.post("/api/meta/sync")
def meta_sync_start() -> Any:
    """Pull fresh results from Meta (read-only GETs) and rebuild winning patterns."""
    from creative_intelligence.ingest.meta_sync import start_sync
    run_id, reason = start_sync(_db)
    if run_id is None:
        return jsonify({"error": reason}), 409
    return jsonify({"run_id": run_id})


@app.get("/api/meta/sync")
def meta_sync_status() -> Any:
    from creative_intelligence.ingest.meta_sync import latest_run
    conn = _db()
    run = latest_run(conn)
    conn.close()
    return jsonify({"run": run})


# ── Prompt preview (free) ────────────────────────────────────────────────
@app.post("/api/adstudio/prompt")
def adstudio_prompt() -> Any:
    from creative_intelligence.adstudio.formats import build_ad_prompt, DEFAULT_ASPECT
    d = request.get_json(force=True, silent=True) or {}
    conn = _db()
    prod = _product_dict(conn, int(d.get("product_id", 0)))
    hook_row = conn.execute("SELECT * FROM adstudio_hooks WHERE id=?", (d.get("hook_id"),)).fetchone()
    conn.close()
    if prod is None or hook_row is None:
        return jsonify({"error": "product or hook not found"}), 404
    try:
        prompt = build_ad_prompt(prod, dict(hook_row), d.get("format_key", ""),
                                 d.get("aspect_ratio", DEFAULT_ASPECT))
        return jsonify({"prompt": prompt})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ── Queue + run generation ───────────────────────────────────────────────
@app.post("/api/adstudio/queue")
def adstudio_queue() -> Any:
    """Create queued ad rows for a batch of (hook_id, format_key) pairs."""
    from creative_intelligence.adstudio.formats import build_ad_prompt, DEFAULT_ASPECT, FORMATS
    d = request.get_json(force=True, silent=True) or {}
    pid = int(d.get("product_id", 0))
    pairs = d.get("pairs") or []
    aspect = d.get("aspect_ratio", DEFAULT_ASPECT)
    conn = _db()
    prod = _product_dict(conn, pid)
    if prod is None:
        conn.close()
        return jsonify({"error": "product not found"}), 404
    hooks = {r["id"]: dict(r) for r in conn.execute(
        "SELECT * FROM adstudio_hooks WHERE product_id=?", (pid,)).fetchall()}
    created = []
    with conn:
        for p in pairs:
            hid = p.get("hook_id"); fkey = p.get("format_key")
            if hid not in hooks or fkey not in FORMATS:
                continue
            prompt = build_ad_prompt(prod, hooks[hid], fkey, aspect)
            cur = conn.execute(
                "INSERT INTO adstudio_ads (product_id, hook_id, format_key, aspect_ratio,"
                " prompt, model, status) VALUES (?,?,?,?,?,?, 'queued')",
                (pid, hid, fkey, aspect, prompt, "google/nano-banana-pro"))
            created.append(cur.lastrowid)
    conn.close()
    return jsonify({"queued": created})


@app.post("/api/adstudio/ad/<int:ad_id>/run")
def adstudio_run_ad(ad_id: int) -> Any:
    """Generate one queued ad. Frontend calls these sequentially (paces rate limits)."""
    from creative_intelligence.adstudio.generate import generate_ad, AdGenError
    from creative_intelligence.adstudio.store import save_ad_image

    conn = _db()
    ad = conn.execute("SELECT * FROM adstudio_ads WHERE id=?", (ad_id,)).fetchone()
    if ad is None:
        conn.close()
        return jsonify({"error": "not found"}), 404
    ad = dict(ad)
    uris = _primary_photo_uris(conn, ad["product_id"], limit=3)
    with conn:
        conn.execute("UPDATE adstudio_ads SET status='generating' WHERE id=?", (ad_id,))
    conn.close()

    try:
        url = generate_ad(ad["prompt"], uris, ad["aspect_ratio"])
        saved = save_ad_image(url, ad["product_id"], ad_id)
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_ads SET status='done', image_path=? WHERE id=?",
                         (saved, ad_id))
        conn.close()
        return jsonify({"id": ad_id, "status": "done"})
    except (AdGenError, Exception) as exc:
        msg = str(exc)[:400]
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_ads SET status='failed', error_message=? WHERE id=?",
                         (msg, ad_id))
        conn.close()
        # 429 = rate limit; tell the frontend to back off and retry
        code = 429 if "429" in msg or "throttled" in msg.lower() else 500
        return jsonify({"id": ad_id, "status": "failed", "error": msg}), code


# ── Gallery ──────────────────────────────────────────────────────────────
@app.get("/api/adstudio/ads")
def adstudio_list_ads() -> Any:
    pid = request.args.get("product_id")
    conn = _db()
    sql = ("SELECT a.id, a.product_id, a.hook_id, a.format_key, a.aspect_ratio,"
           " a.status, a.is_favorite, a.error_message, a.created_at,"
           " a.image_path IS NOT NULL AS has_image, h.headline, h.hook_text"
           " FROM adstudio_ads a LEFT JOIN adstudio_hooks h ON h.id=a.hook_id")
    params: list[Any] = []
    if pid:
        sql += " WHERE a.product_id=?"; params.append(int(pid))
    sql += " ORDER BY a.id DESC LIMIT 300"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return jsonify({"ads": rows})


@app.get("/api/adstudio/ad/<int:ad_id>/image")
def adstudio_ad_image(ad_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT image_path FROM adstudio_ads WHERE id=?", (ad_id,)).fetchone()
    conn.close()
    if row is None or not row["image_path"] or not Path(row["image_path"]).exists():
        return ("", 404)
    return send_file(row["image_path"], mimetype="image/png", conditional=True)


@app.post("/api/adstudio/ad/<int:ad_id>/favorite")
def adstudio_fav_ad(ad_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT is_favorite FROM adstudio_ads WHERE id=?", (ad_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    nv = 0 if row["is_favorite"] else 1
    with conn:
        conn.execute("UPDATE adstudio_ads SET is_favorite=? WHERE id=?", (nv, ad_id))
    conn.close()
    return jsonify({"id": ad_id, "is_favorite": nv})


@app.post("/api/adstudio/ad/<int:ad_id>/delete")
def adstudio_del_ad(ad_id: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_ads WHERE id=?", (ad_id,))
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/adstudio/export")
def adstudio_export() -> Any:
    """Zip up generated ad images. ?product_id= for all done, or ?ids=1,2,3."""
    import io, zipfile
    ids_arg = request.args.get("ids")
    pid = request.args.get("product_id")
    conn = _db()
    if ids_arg:
        ids = [int(x) for x in ids_arg.split(",") if x.strip().isdigit()]
        q = "SELECT id, format_key, image_path FROM adstudio_ads WHERE id IN (%s)" % (
            ",".join("?" * len(ids)))
        rows = conn.execute(q, ids).fetchall()
    elif pid:
        rows = conn.execute(
            "SELECT id, format_key, image_path FROM adstudio_ads"
            " WHERE product_id=? AND image_path IS NOT NULL", (int(pid),)).fetchall()
    else:
        rows = []
    conn.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            p = r["image_path"]
            if p and Path(p).exists():
                z.write(p, arcname=f"ad{r['id']}_{r['format_key']}.png")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="adstudio_ads.zip")


# ─────────────────────────────────────────────
# AD STUDIO v2 — diversity-seed image library + programmatic overlay
# ─────────────────────────────────────────────

@app.post("/api/adstudio/product/<int:pid>/gen-images")
def adstudio_gen_images(pid: int) -> Any:
    """Claude invents N distinct text-free concepts (avoiding prior ones); queue them."""
    from creative_intelligence.adstudio.concepts import generate_concepts, build_image_prompt
    from creative_intelligence.adstudio.formats import DEFAULT_ASPECT
    d = request.get_json(force=True, silent=True) or {}
    n = max(1, min(int(d.get("n", 4)), 12))
    aspect = d.get("aspect_ratio", DEFAULT_ASPECT)

    conn = _db()
    prod = _product_dict(conn, pid)
    if prod is None:
        conn.close(); return jsonify({"error": "product not found"}), 404
    uris = _primary_photo_uris(conn, pid)
    prior = [dict(r) for r in conn.execute(
        "SELECT archetype, title, concept FROM adstudio_images WHERE product_id=?"
        " ORDER BY id DESC LIMIT 60", (pid,)).fetchall()]
    conn.close()

    try:
        concepts = generate_concepts(prod, n, prior, uris)
    except Exception as exc:
        app.logger.exception("gen-images concepts failed")
        return jsonify({"error": str(exc)[:300]}), 500
    if not concepts:
        return jsonify({"error": "no concepts returned"}), 500

    conn = _db()
    created = []
    with conn:
        for cpt in concepts:
            prompt = build_image_prompt(prod, cpt["scene"], aspect)
            cur = conn.execute(
                "INSERT INTO adstudio_images (product_id, archetype, title, concept,"
                " prompt, aspect_ratio, model, status) VALUES (?,?,?,?,?,?,?, 'queued')",
                (pid, cpt["archetype"], cpt["title"], cpt["scene"], prompt, aspect,
                 "google/nano-banana-pro"))
            created.append({"id": cur.lastrowid, "title": cpt["title"],
                            "archetype": cpt["archetype"]})
    conn.close()
    return jsonify({"queued": created})


def _run_image(ad_id_unused=None):  # placeholder to keep symmetry; not used
    pass


@app.post("/api/adstudio/image/<int:img_id>/run")
def adstudio_run_image(img_id: int) -> Any:
    """Generate one queued text-free image (frontend calls these sequentially)."""
    from creative_intelligence.adstudio.generate import generate_ad, AdGenError
    from creative_intelligence.adstudio.store import save_generated_image
    conn = _db()
    row = conn.execute("SELECT * FROM adstudio_images WHERE id=?", (img_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    row = dict(row)
    uris = _primary_photo_uris(conn, row["product_id"], limit=3)
    with conn:
        conn.execute("UPDATE adstudio_images SET status='generating' WHERE id=?", (img_id,))
    conn.close()
    try:
        url = generate_ad(row["prompt"], uris, row["aspect_ratio"])
        saved = save_generated_image(url, row["product_id"], img_id)
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_images SET status='done', image_path=?,"
                         " error_message=NULL WHERE id=?", (saved, img_id))
        conn.close()
        return jsonify({"id": img_id, "status": "done"})
    except (AdGenError, Exception) as exc:
        msg = str(exc)[:400]
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_images SET status='failed', error_message=? WHERE id=?",
                         (msg, img_id))
        conn.close()
        code = 429 if "429" in msg or "throttled" in msg.lower() else 500
        return jsonify({"id": img_id, "status": "failed", "error": msg}), code


@app.post("/api/adstudio/image/<int:img_id>/regenerate")
def adstudio_regen_image(img_id: int) -> Any:
    """Re-run a concept, optionally with operator feedback ('fix') folded in.

    Rebuilds the prompt from the stored concept with the CURRENT builders, so
    prompt improvements (label lock, ingredient accuracy) and the fix note both
    apply — instead of blindly re-running the stale stored prompt.
    """
    from creative_intelligence.adstudio.concepts import build_image_prompt
    d = request.get_json(force=True, silent=True) or {}
    fix = (d.get("fix") or "").strip()

    conn = _db()
    row = conn.execute("SELECT * FROM adstudio_images WHERE id=?", (img_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    row = dict(row)
    prod = _product_dict(conn, row["product_id"])
    new_prompt = row["prompt"]
    if prod and row.get("concept"):
        try:
            new_prompt = build_image_prompt(prod, row["concept"], row["aspect_ratio"], fix)
        except Exception:
            pass
    with conn:
        conn.execute("UPDATE adstudio_images SET status='queued', error_message=NULL,"
                     " prompt=? WHERE id=?", (new_prompt, img_id))
    conn.close()
    return adstudio_run_image(img_id)


@app.get("/api/adstudio/images")
def adstudio_list_images() -> Any:
    pid = request.args.get("product_id")
    include_archived = request.args.get("include_archived") == "1"
    conn = _db()
    sql = ("SELECT id, product_id, archetype, title, status, is_favorite, archived,"
           " aspect_ratio, error_message, created_at, image_path IS NOT NULL AS has_image"
           " FROM adstudio_images WHERE core_id IS NULL")
    params: list[Any] = []
    if pid:
        sql += " AND product_id=?"; params.append(int(pid))
    if not include_archived:
        sql += " AND archived=0"
    sql += " ORDER BY id DESC LIMIT 400"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return jsonify({"images": rows})


def _bulk_apply(table: str, action: str, ids: list[int]) -> int:
    """Apply a bulk action to rows of a table; returns affected count."""
    from datetime import datetime as _dt
    ids = [int(x) for x in ids if str(x).isdigit()]
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    if table in ("adstudio_composites", "adstudio_textads") and action in _REVIEW_SQL:
        # AI ads only go ready once their text has been checked
        gate = " AND text_checked=1" if table == "adstudio_textads" and action == "ready" else ""
        conn = _db()
        with conn:
            cur = conn.execute(f"UPDATE {table} SET {_REVIEW_SQL[action]},"
                               f" reviewed_at=? WHERE id IN ({ph}){gate}", [_dt.utcnow().isoformat(), *ids])
        conn.close()
        return cur.rowcount
    ops = {
        "favorite":   f"UPDATE {table} SET is_favorite=1 WHERE id IN ({ph})",
        "unfavorite": f"UPDATE {table} SET is_favorite=0 WHERE id IN ({ph})",
        "archive":    f"UPDATE {table} SET archived=1 WHERE id IN ({ph})",
        "unarchive":  f"UPDATE {table} SET archived=0 WHERE id IN ({ph})",
        "delete":     f"DELETE FROM {table} WHERE id IN ({ph})",
    }
    if action not in ops:
        raise ValueError(f"bad action {action!r}")
    conn = _db()
    with conn:
        cur = conn.execute(ops[action], ids)
    conn.close()
    return cur.rowcount


# Gallery review states. Ready implies approved; rejecting clears ready.
_REVIEW_SQL = {
    "approve": "review_status='approved'",
    "reject":  "review_status='rejected', is_ready=0",
    "ready":   "review_status='approved', is_ready=1",
    "unready": "is_ready=0",
    "clear":   "review_status='', is_ready=0",
}


@app.post("/api/adstudio/composite/<int:comp_id>/review")
def adstudio_review_composite(comp_id: int) -> Any:
    """
    Body: {action: approve|reject|ready|clear, notes?}. approve / reject /
    ready toggle: repeating the current state clears it (as in Render).
    """
    from datetime import datetime as _dt
    d = request.get_json(force=True, silent=True) or {}
    action = d.get("action", "")
    conn = _db()
    row = conn.execute("SELECT review_status, is_ready FROM adstudio_composites WHERE id=?",
                       (comp_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    if action == "approve" and row["review_status"] == "approved" and not row["is_ready"]:
        action = "clear"
    elif action == "reject" and row["review_status"] == "rejected":
        action = "clear"
    elif action == "ready" and row["is_ready"]:
        action = "unready"
    if action and action not in _REVIEW_SQL:
        conn.close(); return jsonify({"error": f"bad action {action!r}"}), 400
    with conn:
        if action:
            conn.execute(f"UPDATE adstudio_composites SET {_REVIEW_SQL[action]}, reviewed_at=?"
                         " WHERE id=?", (_dt.utcnow().isoformat(), comp_id))
        if "notes" in d:
            conn.execute("UPDATE adstudio_composites SET review_notes=? WHERE id=?",
                         ((d.get("notes") or "").strip(), comp_id))
    r = dict(conn.execute("SELECT id, review_status, is_ready, review_notes FROM adstudio_composites"
                          " WHERE id=?", (comp_id,)).fetchone())
    conn.close()
    return jsonify(r)


@app.post("/api/adstudio/images/bulk")
def adstudio_images_bulk() -> Any:
    d = request.get_json(force=True, silent=True) or {}
    try:
        n = _bulk_apply("adstudio_images", d.get("action", ""), d.get("ids", []))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"affected": n})


@app.post("/api/adstudio/composites/bulk")
def adstudio_composites_bulk() -> Any:
    d = request.get_json(force=True, silent=True) or {}
    try:
        n = _bulk_apply("adstudio_composites", d.get("action", ""), d.get("ids", []))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"affected": n})


@app.get("/api/adstudio/image-file/<int:img_id>")
def adstudio_image_file(img_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT image_path FROM adstudio_images WHERE id=?", (img_id,)).fetchone()
    conn.close()
    if row is None or not row["image_path"] or not Path(row["image_path"]).exists():
        return ("", 404)
    return send_file(row["image_path"], mimetype="image/png", conditional=True)


@app.post("/api/adstudio/image/<int:img_id>/favorite")
def adstudio_fav_image(img_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT is_favorite FROM adstudio_images WHERE id=?", (img_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    nv = 0 if row["is_favorite"] else 1
    with conn:
        conn.execute("UPDATE adstudio_images SET is_favorite=? WHERE id=?", (nv, img_id))
    conn.close()
    return jsonify({"id": img_id, "is_favorite": nv})


@app.post("/api/adstudio/image/<int:img_id>/delete")
def adstudio_del_image(img_id: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_images WHERE id=?", (img_id,))
    conn.close()
    return jsonify({"ok": True})


# ── Compose: draw copy onto a chosen text-free image ─────────────────────
def _core_as_image(conn, core_id: int) -> int | None:
    """
    The adstudio_images id standing in for a finished Core shot, created on
    first use, so Compose / Gallery / exports treat it like any base image.
    Mirror rows are hidden from the Images tab (core_id IS NOT NULL).
    """
    from creative_intelligence.adstudio.core import CORE_KINDS
    hit = conn.execute("SELECT id FROM adstudio_images WHERE core_id=?", (core_id,)).fetchone()
    if hit:
        return hit["id"]
    c = conn.execute("SELECT * FROM adstudio_core WHERE id=? AND image_path IS NOT NULL",
                     (core_id,)).fetchone()
    if c is None or c["kind"] not in CORE_KINDS:
        return None
    with conn:
        cur = conn.execute(
            "INSERT INTO adstudio_images (product_id, archetype, title, concept, prompt,"
            " aspect_ratio, model, status, image_path, core_id)"
            " VALUES (?, 'core', ?, '', '', ?, 'core', 'done', ?, ?)",
            (c["product_id"], f"{CORE_KINDS[c['kind']]} #{core_id}", c["aspect_ratio"],
             c["image_path"], core_id))
    return cur.lastrowid


@app.post("/api/adstudio/compose")
def adstudio_compose() -> Any:
    """Body: {image_id | core_id, headline, subhead, cta, hook_id?, layout}."""
    from creative_intelligence.adstudio.overlay import compose
    from creative_intelligence.adstudio.store import composites_dir
    import json as _json
    d = request.get_json(force=True, silent=True) or {}
    conn = _db()
    image_id = int(d.get("image_id") or 0)
    if d.get("core_id"):
        image_id = _core_as_image(conn, int(d["core_id"])) or 0
    img = conn.execute("SELECT * FROM adstudio_images WHERE id=?", (image_id,)).fetchone()
    if img is None or not img["image_path"] or not Path(img["image_path"]).exists():
        conn.close(); return jsonify({"error": "base image not found"}), 404
    img = dict(img)

    # copy: explicit fields override; else pull from a hook
    headline = d.get("headline", ""); subhead = d.get("subhead", ""); cta = d.get("cta", "")
    hook_id = d.get("hook_id")
    if hook_id and not (headline or subhead or cta):
        h = conn.execute("SELECT headline, hook_text, subhead, cta FROM adstudio_hooks WHERE id=?",
                         (hook_id,)).fetchone()
        if h:
            headline = h["headline"] or h["hook_text"] or ""
            subhead = h["subhead"] or ""; cta = h["cta"] or ""
    layout = d.get("layout") or {}

    with conn:
        cur = conn.execute(
            "INSERT INTO adstudio_composites (product_id, image_id, hook_id, headline,"
            " subhead, cta, layout_json) VALUES (?,?,?,?,?,?,?)",
            (img["product_id"], image_id, hook_id, headline, subhead, cta, _json.dumps(layout)))
    comp_id = cur.lastrowid
    try:
        out = str(composites_dir() / f"comp{comp_id}.png")
        compose(img["image_path"], out, headline=headline, subhead=subhead, cta=cta, layout=layout)
        with conn:
            conn.execute("UPDATE adstudio_composites SET output_path=? WHERE id=?", (out, comp_id))
        conn.close()
        return jsonify({"id": comp_id})
    except Exception as exc:
        app.logger.exception("compose failed")
        conn.close()
        return jsonify({"error": str(exc)[:300]}), 500


@app.post("/api/adstudio/compose-bulk")
def adstudio_compose_bulk() -> Any:
    """Compose copy onto many images at once.

    Body: {product_id, layout, image_ids?(default: all done images),
           core_ids?[Core shots to include], copies?[{headline,subhead,cta,hook_id}]
           OR hook_ids?[...] OR single headline/subhead/cta}. Every copy is
           drawn onto every image.
    """
    from creative_intelligence.adstudio.overlay import compose
    from creative_intelligence.adstudio.store import composites_dir
    import json as _json
    d = request.get_json(force=True, silent=True) or {}
    pid = int(d.get("product_id", 0))
    layout = d.get("layout") or {}
    conn = _db()

    core_img_ids = [i for i in (_core_as_image(conn, int(c)) for c in d.get("core_ids") or []) if i]
    if d.get("image_ids") or core_img_ids:
        ids = [int(x) for x in d.get("image_ids") or []] + core_img_ids
        q = ("SELECT id, image_path FROM adstudio_images WHERE image_path IS NOT NULL"
             " AND id IN (%s)" % ",".join("?" * len(ids)))
        imgs = [dict(r) for r in conn.execute(q, ids).fetchall()]
    else:
        imgs = [dict(r) for r in conn.execute(
            "SELECT id, image_path FROM adstudio_images WHERE product_id=?"
            " AND image_path IS NOT NULL AND core_id IS NULL", (pid,)).fetchall()]

    copies = d.get("copies")
    if not copies:
        if d.get("hook_ids"):
            hooks = {r["id"]: dict(r) for r in conn.execute(
                "SELECT * FROM adstudio_hooks WHERE product_id=?", (pid,)).fetchall()}
            copies = [{
                "hook_id": hid,
                "headline": hooks[hid].get("headline") or hooks[hid].get("hook_text") or "",
                "subhead": hooks[hid].get("subhead", ""),
                "cta": hooks[hid].get("cta", ""),
            } for hid in d["hook_ids"] if hid in hooks]
        else:
            copies = [{"headline": d.get("headline", ""), "subhead": d.get("subhead", ""),
                       "cta": d.get("cta", "")}]

    total = len(imgs) * len(copies)
    if total == 0:
        conn.close(); return jsonify({"error": "nothing to compose"}), 400
    if total > 300:
        conn.close(); return jsonify({"error": f"{total} composites is too many at once (max 300)"}), 400

    made = 0
    for im in imgs:
        if not Path(im["image_path"]).exists():
            continue
        for cp in copies:
            with conn:
                cur = conn.execute(
                    "INSERT INTO adstudio_composites (product_id, image_id, hook_id,"
                    " headline, subhead, cta, layout_json) VALUES (?,?,?,?,?,?,?)",
                    (pid, im["id"], cp.get("hook_id"), cp.get("headline", ""),
                     cp.get("subhead", ""), cp.get("cta", ""), _json.dumps(layout)))
            cid = cur.lastrowid
            out = str(composites_dir() / f"comp{cid}.png")
            try:
                compose(im["image_path"], out, headline=cp.get("headline", ""),
                        subhead=cp.get("subhead", ""), cta=cp.get("cta", ""), layout=layout)
                with conn:
                    conn.execute("UPDATE adstudio_composites SET output_path=? WHERE id=?",
                                 (out, cid))
                made += 1
            except Exception:
                app.logger.exception("bulk compose item failed")
    conn.close()
    return jsonify({"made": made, "total": total})


@app.get("/api/adstudio/composites")
def adstudio_list_composites() -> Any:
    pid = request.args.get("product_id")
    include_archived = request.args.get("include_archived") == "1"
    conn = _db()
    review = request.args.get("review", "")
    sql = ("SELECT c.id, c.product_id, c.image_id, c.hook_id, c.headline, c.subhead, c.cta,"
           " c.is_favorite, c.archived, c.created_at, c.review_status, c.is_ready, c.review_notes,"
           " c.output_path IS NOT NULL AS has_image, h.source AS hook_source,"
           " h.archetype AS hook_archetype, h.pattern_id, i.core_id"
           " FROM adstudio_composites c LEFT JOIN adstudio_hooks h ON h.id = c.hook_id"
           " LEFT JOIN adstudio_images i ON i.id = c.image_id WHERE 1=1")
    params: list[Any] = []
    if pid:
        sql += " AND c.product_id=?"; params.append(int(pid))
    if not include_archived:
        sql += " AND c.archived=0"
    review_where = {"unreviewed": " AND COALESCE(c.review_status,'')=''",
                    "approved": " AND c.review_status='approved'",
                    "ready": " AND c.is_ready=1",
                    "rejected": " AND c.review_status='rejected'"}
    sql += review_where.get(review, "")
    sql += " ORDER BY c.id DESC LIMIT 400"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    csql = ("SELECT COALESCE(review_status,'') AS s, is_ready FROM adstudio_composites"
            " WHERE (? IS NULL OR product_id=?)" + ("" if include_archived else " AND archived=0"))
    counts = {"all": 0, "unreviewed": 0, "approved": 0, "ready": 0, "rejected": 0}
    for r in conn.execute(csql, (pid, int(pid) if pid else None)).fetchall():
        counts["all"] += 1
        counts[r["s"] or "unreviewed"] = counts.get(r["s"] or "unreviewed", 0) + 1
        counts["ready"] += 1 if r["is_ready"] else 0
    conn.close()
    return jsonify({"composites": rows, "counts": counts})


@app.get("/api/adstudio/composite/<int:comp_id>/image")
def adstudio_composite_image(comp_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT output_path FROM adstudio_composites WHERE id=?", (comp_id,)).fetchone()
    conn.close()
    if row is None or not row["output_path"] or not Path(row["output_path"]).exists():
        return ("", 404)
    return send_file(row["output_path"], mimetype="image/png", conditional=True)


@app.post("/api/adstudio/composite/<int:comp_id>/favorite")
def adstudio_fav_composite(comp_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT is_favorite FROM adstudio_composites WHERE id=?", (comp_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    nv = 0 if row["is_favorite"] else 1
    with conn:
        conn.execute("UPDATE adstudio_composites SET is_favorite=? WHERE id=?", (nv, comp_id))
    conn.close()
    return jsonify({"id": comp_id, "is_favorite": nv})


@app.post("/api/adstudio/composite/<int:comp_id>/delete")
def adstudio_del_composite(comp_id: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_composites WHERE id=?", (comp_id,))
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/adstudio/export-composites")
def adstudio_export_composites() -> Any:
    """
    Zip a product's finished ads. ?ready=1 → launch kit: only ads marked
    ready, plus manifest.csv (file, copy, primary text, pattern, notes) for
    uploading to Ads Manager.
    """
    import csv, io, re as _re, zipfile
    pid = request.args.get("product_id")
    ready = request.args.get("ready") == "1"
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        "SELECT c.id, c.output_path, c.headline, c.subhead, c.cta, c.review_notes,"
        " h.body, h.hook_text, h.archetype, h.source, i.core_id"
        " FROM adstudio_composites c LEFT JOIN adstudio_hooks h ON h.id = c.hook_id"
        " LEFT JOIN adstudio_images i ON i.id = c.image_id"
        " WHERE c.product_id=? AND c.output_path IS NOT NULL"
        + (" AND c.is_ready=1" if ready else "") + " ORDER BY c.id",
        (int(pid),)).fetchall()] if pid else []
    prod = _product_dict(conn, int(pid)) if pid else None
    conn.close()
    slug = _re.sub(r"[^a-z0-9]+", "-", ((prod or {}).get("name") or "product").lower()).strip("-")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = io.StringIO()
        w = csv.writer(manifest)
        w.writerow(["file", "headline", "subhead", "cta", "primary_text", "hook",
                    "pattern", "base", "notes"])
        for r in rows:
            if not Path(r["output_path"]).exists():
                continue
            name = f"{slug}_ad_{r['id']}.png"
            z.write(r["output_path"], arcname=name)
            pattern = r["archetype"] if (r["source"] or "").startswith("pattern") else ""
            w.writerow([name, r["headline"], r["subhead"], r["cta"], r["body"] or "",
                        r["hook_text"] or "", pattern, "core shot" if r["core_id"] else "image",
                        r["review_notes"] or ""])
        if ready:
            z.writestr("manifest.csv", manifest.getvalue())
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{slug}_{'launch_kit' if ready else 'ads'}.zip")


# ── AI Ads: finished ads with the copy drawn in (brief → 12 variants → scenes) ──
def _brief_row(conn, bid: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM adstudio_briefs WHERE id=?", (bid,)).fetchone()
    if r is None:
        return None
    b = dict(r)
    b["brief"] = json.loads(b.pop("brief_json") or "{}")
    b["pattern_ids"] = json.loads(b.get("pattern_ids") or "[]")
    return b


def _brief_patterns(conn, b: dict[str, Any]) -> list[dict[str, Any]]:
    """The patterns a brief was written from (re-read for its scenes)."""
    from creative_intelligence.adstudio.winning import get_pattern
    ids = [b["pattern_id"]] if b.get("pattern_id") else b.get("pattern_ids") or []
    return [p for p in (get_pattern(conn, int(i)) for i in ids) if p]


@app.get("/api/adstudio/product/<int:pid>/briefs")
def adstudio_list_briefs(pid: int) -> Any:
    conn = _db()
    rows = conn.execute(
        "SELECT b.id, b.created_at, b.brief_json, b.idea,"
        " (SELECT COUNT(*) FROM adstudio_textads t WHERE t.brief_id=b.id) AS n_ads"
        " FROM adstudio_briefs b WHERE b.product_id=? ORDER BY b.id DESC", (pid,)).fetchall()
    conn.close()
    out = [{"id": r["id"], "created_at": r["created_at"], "idea": r["idea"], "n_ads": r["n_ads"],
            "angle": json.loads(r["brief_json"] or "{}").get("angle", "")} for r in rows]
    return jsonify({"briefs": out})


@app.post("/api/adstudio/product/<int:pid>/brief/draft")
def adstudio_draft_brief(pid: int) -> Any:
    """Body: {pattern: auto|none|<id>, idea?, review_text?, review_author?}. Saves the draft."""
    from creative_intelligence.adstudio.textads import draft_brief
    from creative_intelligence.adstudio.winning import current_patterns, get_pattern
    d = request.get_json(force=True, silent=True) or {}
    mode = str(d.get("pattern", "auto"))
    idea = (d.get("idea") or "").strip()
    review = (d.get("review_text") or "").strip()
    author = (d.get("review_author") or "").strip()
    conn = _db()
    prod = _product_dict(conn, pid)
    uris = _primary_photo_uris(conn, pid)
    if mode == "none":
        patterns = []
    elif mode.isdigit():
        one = get_pattern(conn, int(mode))
        patterns = [one] if one else []
    else:
        patterns = current_patterns(conn, limit=4)
    conn.close()
    if prod is None:
        return jsonify({"error": "product not found"}), 404
    if not patterns and not idea:
        return jsonify({"error": "No winning patterns to build on — sync from Meta, or type an idea to test."}), 400
    try:
        brief = draft_brief(prod, patterns, idea, bool(review), uris)
    except Exception as exc:
        app.logger.exception("draft brief failed")
        return jsonify({"error": str(exc)[:300]}), 500
    if not brief["variants"]:
        return jsonify({"error": "Claude returned no usable brief — try again."}), 500
    labels = {p["id"]: p["label"] for p in patterns}
    brief["pattern_label"] = labels.get(brief["pattern_id"], "")
    conn = _db()
    with conn:
        cur = conn.execute(
            "INSERT INTO adstudio_briefs (product_id, pattern_id, pattern_ids, idea, review_text,"
            " review_author, brief_json) VALUES (?,?,?,?,?,?,?)",
            (pid, brief["pattern_id"], json.dumps([p["id"] for p in patterns]), idea, review,
             author, json.dumps(brief)))
    b = _brief_row(conn, cur.lastrowid)
    conn.close()
    return jsonify(b)


@app.get("/api/adstudio/brief/<int:bid>")
def adstudio_get_brief(bid: int) -> Any:
    conn = _db()
    b = _brief_row(conn, bid)
    conn.close()
    return jsonify(b) if b else (jsonify({"error": "not found"}), 404)


@app.post("/api/adstudio/brief/<int:bid>/update")
def adstudio_update_brief(bid: int) -> Any:
    """Body: {brief: {...}, review_text?, review_author?} — the operator's edits."""
    from creative_intelligence.adstudio.textads import normalize_brief
    d = request.get_json(force=True, silent=True) or {}
    conn = _db()
    b = _brief_row(conn, bid)
    if b is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    merged = {**b["brief"], **(d.get("brief") or {})}
    nb = normalize_brief(merged)
    nb["pattern_id"] = b["brief"].get("pattern_id")
    nb["pattern_label"] = b["brief"].get("pattern_label", "")
    with conn:
        conn.execute("UPDATE adstudio_briefs SET brief_json=?, review_text=?, review_author=? WHERE id=?",
                     (json.dumps(nb), (d.get("review_text", b["review_text"]) or "").strip(),
                      (d.get("review_author", b["review_author"]) or "").strip(), bid))
    out = _brief_row(conn, bid)
    conn.close()
    return jsonify(out)


@app.post("/api/adstudio/brief/<int:bid>/delete")
def adstudio_delete_brief(bid: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_textads WHERE brief_id=?", (bid,))
        conn.execute("DELETE FROM adstudio_briefs WHERE id=?", (bid,))
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/adstudio/brief/<int:bid>/variants")
def adstudio_queue_variants(bid: int) -> Any:
    """Queue the fixed formats for this brief (one ad each). Body: {aspect_ratio?}."""
    from creative_intelligence.adstudio.textads import DEFAULT_ASPECT, VARIANTS, variants_for
    from creative_intelligence.adstudio.generate import MODEL as IMG_MODEL
    d = request.get_json(force=True, silent=True) or {}
    aspect = d.get("aspect_ratio") or DEFAULT_ASPECT
    conn = _db()
    b = _brief_row(conn, bid)
    if b is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    prod = _product_dict(conn, b["product_id"])
    br = b["brief"]
    texts = br.get("primary_texts") or [""]
    run = conn.execute("SELECT COUNT(*) FROM adstudio_textads WHERE brief_id=? AND kind='variant'",
                       (bid,)).fetchone()[0]
    keys = variants_for(prod or {}, bool(b["review_text"]))
    queued = []
    with conn:
        for i, k in enumerate(keys):
            v = br.get("variants", {}).get(k, {})
            fields = VARIANTS[k]["fields"]
            cur = conn.execute(
                "INSERT INTO adstudio_textads (product_id, brief_id, kind, variant, title, headline,"
                " subhead, cta, body, style_seed, aspect_ratio, model, status)"
                " VALUES (?,?,'variant',?,?,?,?,?,?,?,?,?,'queued')",
                (b["product_id"], bid, k, VARIANTS[k]["name"],
                 v.get("headline", "") if "headline" in fields else "",
                 v.get("sub", "") if "sub" in fields else "",
                 br.get("cta", "") if "cta" in fields else "",
                 texts[i % len(texts)], bid * 31 + run + i, aspect, IMG_MODEL))
            queued.append({"id": cur.lastrowid, "variant": k})
    conn.close()
    return jsonify({"queued": queued})


@app.post("/api/adstudio/brief/<int:bid>/scenes")
def adstudio_queue_scenes(bid: int) -> Any:
    """Claude invents N new scenes, each with copy written for it. Body: {n, aspect_ratio?}."""
    from creative_intelligence.adstudio.textads import DEFAULT_ASPECT, invent_scenes
    from creative_intelligence.adstudio.generate import MODEL as IMG_MODEL
    d = request.get_json(force=True, silent=True) or {}
    n = max(1, min(int(d.get("n", 4)), 12))
    aspect = d.get("aspect_ratio") or DEFAULT_ASPECT
    conn = _db()
    b = _brief_row(conn, bid)
    if b is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    pid = b["product_id"]
    prod = _product_dict(conn, pid)
    uris = _primary_photo_uris(conn, pid)
    patterns = _brief_patterns(conn, b)
    prior = [dict(r) for r in conn.execute(
        "SELECT archetype, title, concept, created_at FROM ("
        " SELECT variant AS archetype, title, concept, created_at, id FROM adstudio_textads"
        "  WHERE product_id=? AND kind='scene'"
        " UNION ALL SELECT archetype, title, concept, created_at, id FROM adstudio_images"
        "  WHERE product_id=? AND core_id IS NULL)"
        " ORDER BY created_at DESC, id DESC LIMIT 60", (pid, pid)).fetchall()]
    conn.close()
    try:
        scenes = invent_scenes(prod, b["brief"], n, prior, patterns, b["idea"], uris)
    except Exception as exc:
        app.logger.exception("invent scenes failed")
        return jsonify({"error": str(exc)[:300]}), 500
    if not scenes:
        return jsonify({"error": "no concepts returned"}), 500
    conn = _db()
    queued = []
    with conn:
        for sc in scenes:
            cur = conn.execute(
                "INSERT INTO adstudio_textads (product_id, brief_id, kind, variant, title, concept,"
                " text_layout, headline, subhead, cta, body, aspect_ratio, model, status)"
                " VALUES (?,?,'scene',?,?,?,?,?,?,?,?,?,?,'queued')",
                (pid, bid, sc["archetype"], sc["title"], sc["scene"], sc["text_layout"],
                 sc["headline"], sc["sub"], sc["cta"], sc["body"], aspect, IMG_MODEL))
            queued.append({"id": cur.lastrowid, "title": sc["title"]})
    conn.close()
    return jsonify({"queued": queued})


def _textad_prompt(conn, row: dict[str, Any]) -> str:
    """Build the prompt from the row's CURRENT copy + fix note (so edits apply)."""
    from creative_intelligence.adstudio import textads as T
    prod = _product_dict(conn, row["product_id"]) or {}
    if row["kind"] == "scene":
        return T.build_scene_prompt(prod, row["concept"], row["headline"], row["subhead"],
                                    row["cta"], row["text_layout"], row["aspect_ratio"], row["fix"] or "")
    b = _brief_row(conn, row["brief_id"]) or {"brief": {}, "review_text": "", "review_author": ""}
    return T.build_variant_prompt(prod, b["brief"], row["variant"], row["headline"], row["subhead"],
                                  row["cta"], b["review_text"], b["review_author"], row["style_seed"],
                                  row["aspect_ratio"], row["fix"] or "")


@app.post("/api/adstudio/textad/<int:ad_id>/run")
def adstudio_run_textad(ad_id: int) -> Any:
    """Generate one queued AI ad (the frontend calls these one at a time)."""
    from creative_intelligence.adstudio.generate import generate_ad, AdGenError
    from creative_intelligence.adstudio.store import save_generated_image, textads_dir
    conn = _db()
    row = conn.execute("SELECT * FROM adstudio_textads WHERE id=?", (ad_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    row = dict(row)
    prompt = _textad_prompt(conn, row)
    uris = _primary_photo_uris(conn, row["product_id"], limit=3)
    with conn:
        conn.execute("UPDATE adstudio_textads SET status='generating', prompt=? WHERE id=?",
                     (prompt, ad_id))
    conn.close()
    try:
        url = generate_ad(prompt, uris, row["aspect_ratio"])
        saved = save_generated_image(url, row["product_id"], ad_id, textads_dir())
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_textads SET status='done', image_path=?, error_message=NULL"
                         " WHERE id=?", (saved, ad_id))
        conn.close()
        return jsonify({"id": ad_id, "status": "done"})
    except (AdGenError, Exception) as exc:
        msg = str(exc)[:400]
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_textads SET status='failed', error_message=? WHERE id=?",
                         (msg, ad_id))
        conn.close()
        code = 429 if "429" in msg or "throttled" in msg.lower() else 500
        return jsonify({"id": ad_id, "status": "failed", "error": msg}), code


@app.post("/api/adstudio/textad/<int:ad_id>/regenerate")
def adstudio_regen_textad(ad_id: int) -> Any:
    """
    Body: {fix?, headline?, subhead?, cta?, run?}. Saves the fix note and any
    copy edits, clears the old review (it's a new image), then re-runs unless
    run=false.
    """
    d = request.get_json(force=True, silent=True) or {}
    conn = _db()
    if conn.execute("SELECT 1 FROM adstudio_textads WHERE id=?", (ad_id,)).fetchone() is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    sets, vals = ["status='queued'", "error_message=NULL", "review_status=''", "is_ready=0",
                  "text_checked=0"], []
    for k in ("fix", "headline", "subhead", "cta"):
        if k in d:
            sets.append(f"{k}=?"); vals.append((d.get(k) or "").strip())
    with conn:
        conn.execute(f"UPDATE adstudio_textads SET {', '.join(sets)} WHERE id=?", (*vals, ad_id))
    conn.close()
    if d.get("run", True) is False:
        return jsonify({"id": ad_id, "status": "queued"})
    return adstudio_run_textad(ad_id)


_TEXTAD_COLS = ("t.id, t.product_id, t.brief_id, t.kind, t.variant, t.title, t.headline, t.subhead,"
                " t.cta, t.body, t.fix, t.status, t.error_message, t.is_favorite, t.archived,"
                " t.review_status, t.is_ready, t.text_checked, t.review_notes, t.aspect_ratio,"
                " t.created_at, t.image_path IS NOT NULL AS has_image")


_EXPORTED_SQL = ("SELECT 1 FROM adstudio_drive_export_items i JOIN adstudio_drive_exports e"
                 " ON e.id=i.export_id WHERE i.textad_id=t.id")


def _attach_exports(conn, rows: list[dict[str, Any]]) -> None:
    """Each ad's Drive exports, newest first: [{folder_name, folder_url, at}]."""
    ids = [r["id"] for r in rows]
    by: dict[int, list] = {i: [] for i in ids}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for x in conn.execute(
                "SELECT i.textad_id, e.folder_name, e.folder_url, i.uploaded_at FROM adstudio_drive_export_items i"
                " JOIN adstudio_drive_exports e ON e.id=i.export_id"
                f" WHERE i.textad_id IN ({','.join('?' * len(chunk))}) ORDER BY i.id DESC", chunk):
            by[x["textad_id"]].append({"folder_name": x["folder_name"], "folder_url": x["folder_url"],
                                       "at": x["uploaded_at"]})
    for r in rows:
        r["exports"] = by.get(r["id"], [])


@app.get("/api/adstudio/textads")
def adstudio_list_textads() -> Any:
    """?product_id [&brief_id] [&done=1 (gallery)] [&review=…] [&include_archived=1]."""
    pid = request.args.get("product_id")
    bid = request.args.get("brief_id")
    done = request.args.get("done") == "1"
    include_archived = request.args.get("include_archived") == "1"
    where, params = ["1=1"], []
    if pid:
        where.append("t.product_id=?"); params.append(int(pid))
    if bid:
        where.append("t.brief_id=?"); params.append(int(bid))
    if done:
        where.append("t.status='done' AND t.image_path IS NOT NULL")
    if not include_archived:
        where.append("t.archived=0")
    base = " AND ".join(where)
    review_where = {"unreviewed": " AND COALESCE(t.review_status,'')=''",
                    "approved": " AND t.review_status='approved'",
                    "ready": " AND t.is_ready=1",
                    "rejected": " AND t.review_status='rejected'",
                    "notexported": f" AND NOT EXISTS ({_EXPORTED_SQL})"}
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        f"SELECT {_TEXTAD_COLS}, b.brief_json FROM adstudio_textads t"
        f" LEFT JOIN adstudio_briefs b ON b.id=t.brief_id WHERE {base}"
        + review_where.get(request.args.get("review", ""), "")
        + " ORDER BY t.id DESC LIMIT 400", params).fetchall()]
    for r in rows:
        bj = json.loads(r.pop("brief_json") or "{}")
        r["angle"], r["pattern_label"] = bj.get("angle", ""), bj.get("pattern_label", "")
    _attach_exports(conn, rows)
    counts = {"all": 0, "unreviewed": 0, "approved": 0, "ready": 0, "rejected": 0, "notexported": 0}
    for r in conn.execute(f"SELECT COALESCE(t.review_status,'') AS s, t.is_ready,"
                          f" EXISTS ({_EXPORTED_SQL}) AS exported FROM adstudio_textads t"
                          f" WHERE {base}", params).fetchall():
        counts["all"] += 1
        counts[r["s"] or "unreviewed"] = counts.get(r["s"] or "unreviewed", 0) + 1
        counts["ready"] += 1 if r["is_ready"] else 0
        counts["notexported"] += 0 if r["exported"] else 1
    conn.close()
    return jsonify({"ads": rows, "counts": counts})


@app.get("/api/adstudio/textad/<int:ad_id>/image")
def adstudio_textad_image(ad_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT image_path FROM adstudio_textads WHERE id=?", (ad_id,)).fetchone()
    conn.close()
    if row is None or not row["image_path"] or not Path(row["image_path"]).exists():
        return ("", 404)
    return send_file(row["image_path"], mimetype="image/png", conditional=True)


@app.post("/api/adstudio/textad/<int:ad_id>/favorite")
def adstudio_fav_textad(ad_id: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT is_favorite FROM adstudio_textads WHERE id=?", (ad_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    nv = 0 if row["is_favorite"] else 1
    with conn:
        conn.execute("UPDATE adstudio_textads SET is_favorite=? WHERE id=?", (nv, ad_id))
    conn.close()
    return jsonify({"id": ad_id, "is_favorite": nv})


@app.post("/api/adstudio/textad/<int:ad_id>/delete")
def adstudio_del_textad(ad_id: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_textads WHERE id=?", (ad_id,))
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/adstudio/textad/<int:ad_id>/review")
def adstudio_review_textad(ad_id: int) -> Any:
    """
    Body: {action?: approve|reject|ready|clear, notes?, text_checked?}. Same
    toggles as the composite Gallery, plus: an ad can only be marked ready once
    its text has been checked (spelling, price, claims) — the AI drew it.
    """
    from datetime import datetime as _dt
    d = request.get_json(force=True, silent=True) or {}
    action = d.get("action", "")
    conn = _db()
    row = conn.execute("SELECT review_status, is_ready, text_checked FROM adstudio_textads WHERE id=?",
                       (ad_id,)).fetchone()
    if row is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    checked = bool(d["text_checked"]) if "text_checked" in d else bool(row["text_checked"])
    if action == "approve" and row["review_status"] == "approved" and not row["is_ready"]:
        action = "clear"
    elif action == "reject" and row["review_status"] == "rejected":
        action = "clear"
    elif action == "ready" and row["is_ready"]:
        action = "unready"
    if action and action not in _REVIEW_SQL:
        conn.close(); return jsonify({"error": f"bad action {action!r}"}), 400
    if action == "ready" and not checked:
        conn.close()
        return jsonify({"error": "Check the text first — tick “text checked” once spelling, "
                                 "prices and claims are right."}), 400
    with conn:
        if "text_checked" in d:
            conn.execute("UPDATE adstudio_textads SET text_checked=?" + ("" if checked else ", is_ready=0")
                         + " WHERE id=?", (int(checked), ad_id))
        if action:
            conn.execute(f"UPDATE adstudio_textads SET {_REVIEW_SQL[action]}, reviewed_at=? WHERE id=?",
                         (_dt.utcnow().isoformat(), ad_id))
        if "notes" in d:
            conn.execute("UPDATE adstudio_textads SET review_notes=? WHERE id=?",
                         ((d.get("notes") or "").strip(), ad_id))
    r = dict(conn.execute("SELECT id, review_status, is_ready, text_checked, review_notes"
                          " FROM adstudio_textads WHERE id=?", (ad_id,)).fetchone())
    conn.close()
    return jsonify(r)


@app.post("/api/adstudio/textads/bulk")
def adstudio_textads_bulk() -> Any:
    d = request.get_json(force=True, silent=True) or {}
    try:
        n = _bulk_apply("adstudio_textads", d.get("action", ""), d.get("ids", []))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"affected": n})


@app.get("/api/adstudio/export-textads")
def adstudio_export_textads() -> Any:
    """
    Zip a product's AI ads. ?ready=1 → launch kit: ready ads only, plus
    manifest.csv (file, on-image copy, primary text, format, angle, pattern,
    notes) for Ads Manager.
    """
    import csv, io, re as _re, zipfile
    pid = request.args.get("product_id")
    ready = request.args.get("ready") == "1"
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        "SELECT t.id, t.image_path, t.kind, t.variant, t.title, t.headline, t.subhead, t.cta,"
        " t.body, t.review_notes, t.aspect_ratio, b.brief_json"
        " FROM adstudio_textads t LEFT JOIN adstudio_briefs b ON b.id=t.brief_id"
        " WHERE t.product_id=? AND t.image_path IS NOT NULL"
        + (" AND t.is_ready=1" if ready else "") + " ORDER BY t.id",
        (int(pid),)).fetchall()] if pid else []
    prod = _product_dict(conn, int(pid)) if pid else None
    conn.close()
    slug = _re.sub(r"[^a-z0-9]+", "-", ((prod or {}).get("name") or "product").lower()).strip("-")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = io.StringIO()
        w = csv.writer(manifest)
        w.writerow(["file", "format", "headline", "subhead", "cta", "primary_text", "angle",
                    "pattern", "aspect_ratio", "notes"])
        for r in rows:
            if not Path(r["image_path"]).exists():
                continue
            name = f"{slug}_ai_{r['id']}.png"
            z.write(r["image_path"], arcname=name)
            bj = json.loads(r["brief_json"] or "{}")
            fmt = r["title"] if r["kind"] == "variant" else f"scene: {r['variant']} — {r['title']}"
            w.writerow([name, fmt, r["headline"], r["subhead"], r["cta"], r["body"] or "",
                        bj.get("angle", ""), bj.get("pattern_label", ""), r["aspect_ratio"],
                        r["review_notes"] or ""])
        if ready:
            z.writestr("manifest.csv", manifest.getvalue())
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{slug}_{'ai_launch_kit' if ready else 'ai_ads'}.zip")


# ── Google Drive: export AI ads to a new folder for outside agencies ───────
_DRIVE_STATES: dict[str, tuple[float, str]] = {}   # OAuth state → (expiry, product id)


def _drive_auth(conn) -> dict[str, Any] | None:
    r = conn.execute("SELECT email, refresh_token, connected_at FROM adstudio_drive_auth WHERE id=1").fetchone()
    return dict(r) if r else None


def _drive_parent() -> str:
    from creative_intelligence import config
    return os.getenv("ADSTUDIO_DRIVE_FOLDER_ID", config.ADSTUDIO_DRIVE_FOLDER_ID)


def _drive_redirect_uri() -> str:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    return f"{proto}://{request.host}/drive/callback"


@app.get("/api/drive/status")
def drive_status() -> Any:
    from creative_intelligence.adstudio import drive
    conn = _db()
    auth = _drive_auth(conn)
    conn.close()
    parent = _drive_parent()
    return jsonify({"configured": drive.configured(), "connected": bool(auth),
                    "email": auth["email"] if auth else "",
                    "folder_url": drive.folder_url(parent) if parent else "",
                    "redirect_uri": _drive_redirect_uri()})


@app.get("/drive/connect")
def drive_connect() -> Any:
    import secrets, time
    from flask import redirect
    from creative_intelligence.adstudio import drive
    if not drive.configured():
        return ("Google Drive isn't set up: add GOOGLE_OAUTH_CLIENT_ID and "
                "GOOGLE_OAUTH_CLIENT_SECRET to the environment (see .env.example).", 400)
    now = time.time()
    for k in [k for k, v in _DRIVE_STATES.items() if v[0] < now]:
        _DRIVE_STATES.pop(k, None)
    state = secrets.token_urlsafe(24)
    pid = request.args.get("product_id", "")
    _DRIVE_STATES[state] = (now + 600, pid if pid.isdigit() else "")
    return redirect(drive.auth_url(_drive_redirect_uri(), state))


@app.get("/drive/callback")
def drive_callback() -> Any:
    import time
    from flask import redirect
    from creative_intelligence.adstudio import drive
    exp, pid = _DRIVE_STATES.pop(request.args.get("state", ""), (0, ""))
    if exp < time.time():
        return ("This sign-in link expired or didn't come from Ad Studio — try Connect again.", 400)
    back = "/adstudio?" + (f"product_id={pid}&" if pid else "")
    if request.args.get("error"):
        return redirect(back + "drive=declined")
    try:
        token, email = drive.exchange_code(request.args.get("code", ""), _drive_redirect_uri())
    except drive.DriveError as exc:
        return (f"Google sign-in failed: {exc}", 400)
    from creative_intelligence import config
    allowed = os.getenv("ADSTUDIO_DRIVE_ACCOUNT", config.ADSTUDIO_DRIVE_ACCOUNT).strip().lower()
    if allowed and email != allowed:
        return (f"Signed in as {email or 'an unknown account'}, but exports may only use {allowed}. "
                "Sign in with that account.", 403)
    conn = _db()
    with conn:
        conn.execute("INSERT OR REPLACE INTO adstudio_drive_auth (id, email, refresh_token, connected_at)"
                     " VALUES (1, ?, ?, datetime('now'))", (email, token))
    conn.close()
    return redirect(back + "drive=connected")


@app.post("/api/drive/disconnect")
def drive_disconnect() -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_drive_auth WHERE id=1")
    conn.close()
    return jsonify({"ok": True})


def _drive_token(conn) -> str:
    from creative_intelligence.adstudio import drive
    auth = _drive_auth(conn)
    if not auth:
        raise drive.DriveError("Google Drive isn't connected — click “Connect Google Drive” first.")
    return drive.access_token(auth["refresh_token"])


@app.post("/api/adstudio/drive-export")
def adstudio_drive_export_start() -> Any:
    """
    Body: {product_id, ids, date: 'YYYY-MM-DD' (the user's local date)}.
    Checks every ad can go to an agency, then makes
    Meta Ads / <Product> / '<date> · <Product> · N AI ads'. The frontend then
    uploads the ads one by one (…/upload/<ad_id>) and calls …/finish.
    """
    import re as _re
    from datetime import date as _date
    from creative_intelligence.adstudio import drive
    d = request.get_json(force=True, silent=True) or {}
    pid = int(d.get("product_id") or 0)
    ids = list(dict.fromkeys(int(x) for x in d.get("ids", []) if str(x).isdigit()))
    day = d.get("date") if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d.get("date", ""))) else _date.today().isoformat()
    if not ids:
        return jsonify({"error": "Select some ads to export."}), 400
    conn = _db()
    prod = _product_dict(conn, pid)
    if prod is None:
        conn.close(); return jsonify({"error": "product not found"}), 404
    ph = ",".join("?" * len(ids))
    rows = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT id, product_id, status, image_path, text_checked, review_status FROM adstudio_textads"
        f" WHERE id IN ({ph})", ids).fetchall()}
    problems = {
        "not found / other product": [i for i in ids if i not in rows or rows[i]["product_id"] != pid],
        "not rendered yet": [i for i in ids if i in rows and (rows[i]["status"] != "done" or not rows[i]["image_path"])],
        "text not checked": [i for i in ids if i in rows and not rows[i]["text_checked"]],
        "rejected": [i for i in ids if i in rows and rows[i]["review_status"] == "rejected"],
    }
    bad = {k: v for k, v in problems.items() if v}
    if bad:
        conn.close()
        return jsonify({"error": "Some selected ads can't go to an agency: "
                                 + "; ".join(f"{len(v)} {k}" for k, v in bad.items()), "problems": bad}), 400
    parent = _drive_parent()
    try:
        token = _drive_token(conn)
        if not drive.get_folder(token, parent):
            raise drive.DriveError("The Meta Ads export folder is missing or in the trash "
                                   "(ADSTUDIO_DRIVE_FOLDER_ID).")
        pfolder = prod.get("drive_folder_id")
        if not pfolder or not drive.get_folder(token, pfolder):
            pname = drive.clean(prod["name"])
            pfolder = drive.find_folder(token, pname, parent) or drive.create_folder(token, pname, parent)
            with conn:
                conn.execute("UPDATE adstudio_products SET drive_folder_id=? WHERE id=?", (pfolder, pid))
        name = drive.export_folder_name(prod["name"], day, len(ids), drive.child_names(token, pfolder))
        fid = drive.create_folder(token, name, pfolder)
    except drive.DriveError as exc:
        conn.close(); return jsonify({"error": str(exc)}), 502
    with conn:
        cur = conn.execute(
            "INSERT INTO adstudio_drive_exports (product_id, folder_id, folder_name, folder_url, n_ads, ad_ids)"
            " VALUES (?,?,?,?,?,?)", (pid, fid, name, drive.folder_url(fid), len(ids), json.dumps(ids)))
    conn.close()
    return jsonify({"export_id": cur.lastrowid, "folder_name": name, "folder_url": drive.folder_url(fid),
                    "product_folder_url": drive.folder_url(pfolder), "ids": ids})


@app.post("/api/adstudio/drive-export/<int:eid>/upload/<int:ad_id>")
def adstudio_drive_export_upload(eid: int, ad_id: int) -> Any:
    from creative_intelligence.adstudio import drive
    conn = _db()
    e = conn.execute("SELECT * FROM adstudio_drive_exports WHERE id=?", (eid,)).fetchone()
    ids = json.loads(e["ad_ids"]) if e else []
    if e is None or ad_id not in ids:
        conn.close(); return jsonify({"error": "not part of this export"}), 404
    done = conn.execute("SELECT file_url FROM adstudio_drive_export_items WHERE export_id=? AND textad_id=?",
                        (eid, ad_id)).fetchone()
    if done:                                  # retry after a dropped response — don't upload twice
        conn.close(); return jsonify({"ok": True, "file_url": done["file_url"]})
    ad = dict(conn.execute("SELECT * FROM adstudio_textads WHERE id=?", (ad_id,)).fetchone())
    prod = _product_dict(conn, e["product_id"]) or {}
    name = drive.ad_file_name(prod.get("name", "ad"), ids.index(ad_id) + 1, ad)
    try:
        if not Path(ad["image_path"] or "").exists():
            raise drive.DriveError("image file is missing on the server")
        f = drive.upload_file(_drive_token(conn), ad["image_path"], name, e["folder_id"])
    except drive.DriveError as exc:
        conn.close(); return jsonify({"error": str(exc)}), 502
    with conn:
        conn.execute("INSERT OR IGNORE INTO adstudio_drive_export_items (export_id, textad_id, file_id,"
                     " file_name, file_url) VALUES (?,?,?,?,?)", (eid, ad_id, f["id"], name, f.get("webViewLink")))
    conn.close()
    return jsonify({"ok": True, "file_url": f.get("webViewLink")})


@app.post("/api/adstudio/drive-export/<int:eid>/finish")
def adstudio_drive_export_finish(eid: int) -> Any:
    """Add the 'Ad copy' Google Sheet (one row per uploaded ad) and close the export."""
    import csv, io
    from creative_intelligence.adstudio import drive
    conn = _db()
    e = conn.execute("SELECT * FROM adstudio_drive_exports WHERE id=?", (eid,)).fetchone()
    if e is None:
        conn.close(); return jsonify({"error": "not found"}), 404
    rows = conn.execute(
        "SELECT i.file_name, i.file_url, t.kind, t.variant, t.title, t.headline, t.subhead, t.cta, t.body,"
        " t.aspect_ratio, b.brief_json FROM adstudio_drive_export_items i"
        " JOIN adstudio_textads t ON t.id=i.textad_id LEFT JOIN adstudio_briefs b ON b.id=t.brief_id"
        " WHERE i.export_id=? ORDER BY i.file_name", (eid,)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    # Agency-facing: no internal review notes.
    w.writerow(["file", "format", "on-image headline", "on-image subhead", "CTA button",
                "primary text", "angle", "aspect ratio", "link"])
    for r in rows:
        bj = json.loads(r["brief_json"] or "{}")
        fmt = r["title"] if r["kind"] == "variant" else f"Scene: {r['title']}"
        w.writerow([r["file_name"], fmt, r["headline"], r["subhead"], r["cta"], r["body"] or "",
                    bj.get("angle", ""), r["aspect_ratio"], r["file_url"] or ""])
    sheet_url = None
    try:
        if rows:
            s = drive.upload(_drive_token(conn), f"Ad copy — {e['folder_name']}", e["folder_id"],
                             buf.getvalue().encode(), "text/csv", as_mime=drive.SHEET)
            sheet_url = s.get("webViewLink")
    except drive.DriveError as exc:
        conn.close(); return jsonify({"error": f"Ads uploaded, but the copy sheet failed: {exc}"}), 502
    with conn:
        conn.execute("UPDATE adstudio_drive_exports SET status=?, sheet_url=?, finished_at=datetime('now')"
                     " WHERE id=?", ("done" if len(rows) == e["n_ads"] else "partial", sheet_url, eid))
    conn.close()
    return jsonify({"uploaded": len(rows), "of": e["n_ads"], "folder_url": e["folder_url"],
                    "folder_name": e["folder_name"], "sheet_url": sheet_url})


# ── Core photos (core1-3) + box-size infographics ───────────────────────────
def _core_row(conn, cid: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM adstudio_core WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def _uri(path: str) -> str | None:
    from creative_intelligence.adstudio.store import to_data_uri
    try:
        return to_data_uri(path)
    except Exception:
        return None


def _core_refs(conn, pid: int, kind: str) -> tuple[list[str], list[str]]:
    """
    (layout_uris, fruit_uris) for a core shot. Layout = this product's photo
    tagged with the same role, else up to 2 from other products (house
    examples). Fruit = this product's remaining real photos. Infographics and
    other-fruit photos are never sent.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT id, path, role FROM adstudio_photos WHERE product_id=?"
        " ORDER BY is_primary DESC, id", (pid,)).fetchall()]
    rows = [r for r in rows if (r["role"] or "") not in ("infographic", "other")]
    own = [r for r in rows if r["role"] == kind][:1]
    layout = own or [dict(r) for r in conn.execute(
        "SELECT MIN(id) AS id, path FROM adstudio_photos WHERE role=? AND product_id!=?"
        " GROUP BY product_id ORDER BY id LIMIT 2", (kind, pid)).fetchall()]
    used = {r["id"] for r in own}
    order = {"core1": 0, "core2": 1, "core3": 2, "": 3}
    fruit = sorted((r for r in rows if r["id"] not in used),
                   key=lambda r: order.get(r["role"] or "", 3))[:max(0, 5 - len(layout))]
    to = lambda rs: [u for u in (_uri(r["path"]) for r in rs) if u]
    return to(layout), to(fruit)


def _tag_photos(conn, pid: int, name: str) -> dict[int, str]:
    """Claude assigns a role to each of the product's photos; primary = core1."""
    from creative_intelligence.adstudio.core import classify_photos
    rows = [dict(r) for r in conn.execute(
        "SELECT id, path FROM adstudio_photos WHERE product_id=? ORDER BY id", (pid,)).fetchall()]
    rows = [r for r in rows if _uri(r["path"])]
    roles = classify_photos(name, [_uri(r["path"]) for r in rows])
    with conn:
        for r, role in zip(rows, roles):
            conn.execute("UPDATE adstudio_photos SET role=? WHERE id=?", (role, r["id"]))
        hero = next((r["id"] for r, role in zip(rows, roles) if role == "core1"), None)
        if hero:
            conn.execute("UPDATE adstudio_photos SET is_primary=(id=?) WHERE product_id=?", (hero, pid))
    return {r["id"]: role for r, role in zip(rows, roles)}


@app.post("/api/adstudio/import-shopify")
def adstudio_import_shopify() -> Any:
    """Create/refresh a product from its Shopify URL: name, copy, variants, photos."""
    from urllib.request import Request, urlopen
    from creative_intelligence.adstudio import core as C
    from creative_intelligence.adstudio.store import save_reference_photo
    d = request.get_json(force=True, silent=True) or {}
    url = (d.get("url") or "").strip()
    n_photos = max(0, min(int(d.get("photos", 8)), 10))
    try:
        sp = C.fetch_shopify_product(url)
    except Exception as exc:
        return jsonify({"error": f"Shopify fetch failed: {str(exc)[:200]}"}), 400
    rows, estimate = C.rows_from_variants(sp["variants"])
    info = {"name": C.short_name(sp["title"]), "rows": rows, "estimate": estimate,
            "bab": C.bab_row([r for r in rows if r["on"]] or rows)}

    conn = _db()
    pid = d.get("product_id")
    if not pid:
        hit = conn.execute("SELECT id FROM adstudio_products WHERE shopify_url LIKE ?",
                           (f"%/products/{sp['handle']}%",)).fetchone()
        pid = hit["id"] if hit else None
    with conn:
        if pid:
            conn.execute("UPDATE adstudio_products SET description=?, shopify_url=?, info_json=?"
                         " WHERE id=?", (sp["description"], url, json.dumps(info), int(pid)))
        else:
            cur = conn.execute(
                "INSERT INTO adstudio_products (name, description, shopify_url, info_json,"
                " product_type) VALUES (?,?,?,?, 'fruit')",
                (sp["title"], sp["description"], url, json.dumps(info)))
            pid = cur.lastrowid
    pid = int(pid)
    has_photos = conn.execute("SELECT 1 FROM adstudio_photos WHERE product_id=? LIMIT 1",
                              (pid,)).fetchone()
    added = 0
    if not has_photos:
        for i, src in enumerate(sp["images"][:n_photos]):
            try:
                sized = src + ("&" if "?" in src else "?") + "width=1200"
                raw = urlopen(Request(sized, headers={"User-Agent": "GHF-AdStudio/1.0"}),  # noqa: S310
                              timeout=30).read()
                path = save_reference_photo(raw, f"shopify{i}.jpg", pid)
                with conn:
                    conn.execute("INSERT INTO adstudio_photos (product_id, path, is_primary)"
                                 " VALUES (?,?,?)", (pid, path, 1 if added == 0 else 0))
                added += 1
            except Exception:
                app.logger.exception("shopify image download failed: %s", src)
    roles: dict[int, str] = {}
    if added:
        try:
            roles = _tag_photos(conn, pid, sp["title"])
        except Exception:
            app.logger.exception("photo tagging failed")
    conn.close()
    return jsonify({"id": pid, "photos_added": added, "roles": roles, "info": info})


@app.post("/api/adstudio/product/<int:pid>/tag-photos")
def adstudio_tag_photos(pid: int) -> Any:
    conn = _db()
    prod = _product_dict(conn, pid)
    if prod is None:
        conn.close(); return jsonify({"error": "product not found"}), 404
    try:
        roles = _tag_photos(conn, pid, prod["name"])
    except Exception as exc:
        conn.close()
        app.logger.exception("tag-photos failed")
        return jsonify({"error": str(exc)[:300]}), 500
    conn.close()
    return jsonify({"roles": roles})


@app.post("/api/adstudio/photo/<int:photo_id>/role")
def adstudio_photo_role(photo_id: int) -> Any:
    from creative_intelligence.adstudio.core import PHOTO_ROLES
    role = (request.get_json(force=True, silent=True) or {}).get("role", "")
    if role and role not in PHOTO_ROLES:
        return jsonify({"error": "bad role"}), 400
    conn = _db()
    with conn:
        conn.execute("UPDATE adstudio_photos SET role=? WHERE id=?", (role, photo_id))
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/adstudio/product/<int:pid>/describe-fruit")
def adstudio_describe_fruit(pid: int) -> Any:
    """Claude drafts the fruit's whole/cut look (+ varieties) from its photos."""
    from creative_intelligence.adstudio.core import describe_fruit
    conn = _db()
    prod = _product_dict(conn, pid)
    uris = _primary_photo_uris(conn, pid, limit=4)
    conn.close()
    if prod is None:
        return jsonify({"error": "product not found"}), 404
    try:
        return jsonify(describe_fruit(prod, uris))
    except Exception as exc:
        app.logger.exception("describe-fruit failed")
        return jsonify({"error": str(exc)[:300]}), 500


@app.post("/api/adstudio/product/<int:pid>/core/queue")
def adstudio_core_queue(pid: int) -> Any:
    """
    Queue core shots: {"kinds": ["core1","core2","core3"], "n": 1}. Claude writes
    a shot brief from the layout + fruit references (one per kind; one per
    lifestyle setup for core3) before anything reaches the image model.
    """
    from creative_intelligence.adstudio.core import (
        CORE_KINDS, DEFAULT_ASPECT, build_core_prompt, write_shot_brief)
    d = request.get_json(force=True, silent=True) or {}
    kinds = [k for k in (d.get("kinds") or list(CORE_KINDS)) if k in CORE_KINDS]
    n = max(1, min(int(d.get("n", 1)), 6))
    conn = _db()
    prod = _product_dict(conn, pid)
    if prod is None:
        conn.close(); return jsonify({"error": "product not found"}), 404
    queued = []
    for k in kinds:
        layout, fruit = _core_refs(conn, pid, k)
        base = conn.execute("SELECT COUNT(*) FROM adstudio_core WHERE product_id=? AND kind=?",
                            (pid, k)).fetchone()[0]
        briefs: dict[int, str] = {}
        for i in range(n):
            variant = base + i
            key = variant % 5 if k == "core3" else 0
            if key not in briefs:
                try:
                    briefs[key] = write_shot_brief(prod, k, variant, layout, fruit)
                except Exception:
                    app.logger.exception("shot brief failed (%s)", k)
                    briefs[key] = ""
            prompt = build_core_prompt(prod, k, variant, brief=briefs[key],
                                       n_layout=len(layout), n_fruit=len(fruit))
            with conn:
                cur = conn.execute(
                    "INSERT INTO adstudio_core (product_id, kind, variant, brief, prompt, aspect_ratio)"
                    " VALUES (?,?,?,?,?,?)", (pid, k, variant, briefs[key], prompt, DEFAULT_ASPECT))
            queued.append({"id": cur.lastrowid, "kind": k})
    conn.close()
    return jsonify({"queued": queued})


@app.post("/api/adstudio/core/<int:cid>/run")
def adstudio_core_run(cid: int) -> Any:
    from creative_intelligence.adstudio.core import build_core_prompt
    from creative_intelligence.adstudio.generate import generate_ad, AdGenError
    from creative_intelligence.adstudio.store import save_generated_image, core_dir
    conn = _db()
    row = _core_row(conn, cid)
    if row is None or row["kind"].startswith("info_"):
        conn.close(); return jsonify({"error": "not found"}), 404
    # rebuilt at run time so the image labels always match what is actually sent
    layout, fruit = _core_refs(conn, row["product_id"], row["kind"])
    prompt = build_core_prompt(_product_dict(conn, row["product_id"]) or {}, row["kind"],
                               row["variant"], row.get("fix") or "", row.get("brief") or "",
                               len(layout), len(fruit))
    with conn:
        conn.execute("UPDATE adstudio_core SET status='generating', prompt=? WHERE id=?",
                     (prompt, cid))
    conn.close()
    try:
        url = generate_ad(prompt, layout + fruit, row["aspect_ratio"])
        saved = save_generated_image(url, row["product_id"], cid)
        dest = core_dir() / f"{row['kind']}_{cid}_p{row['product_id']}.png"
        Path(saved).replace(dest)
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_core SET status='done', image_path=?, error_message=NULL"
                         " WHERE id=?", (str(dest), cid))
        conn.close()
        return jsonify({"id": cid, "status": "done"})
    except (AdGenError, Exception) as exc:
        msg = str(exc)[:400]
        conn = _db()
        with conn:
            conn.execute("UPDATE adstudio_core SET status='failed', error_message=? WHERE id=?",
                         (msg, cid))
        conn.close()
        code = 429 if "429" in msg or "throttled" in msg.lower() else 500
        return jsonify({"id": cid, "status": "failed", "error": msg}), code


@app.post("/api/adstudio/core/<int:cid>/regenerate")
def adstudio_core_regen(cid: int) -> Any:
    """Re-run a core shot with an operator fix note (kept for later reruns)."""
    d = request.get_json(force=True, silent=True) or {}
    conn = _db()
    row = _core_row(conn, cid)
    if row is None or row["kind"].startswith("info_"):
        conn.close(); return jsonify({"error": "not found"}), 404
    with conn:
        conn.execute("UPDATE adstudio_core SET status='queued', error_message=NULL, fix=?"
                     " WHERE id=?", ((d.get("fix") or "").strip(), cid))
    conn.close()
    return adstudio_core_run(cid)


@app.post("/api/adstudio/product/<int:pid>/infographic")
def adstudio_infographic(pid: int) -> Any:
    """
    Render a box-size infographic. Body: {"kind": "pdp"|"bab", "info": {...},
    "thumb": "core:<id>" | "photo:<id>" | ""}. `info` is saved on the product.
    """
    from creative_intelligence.adstudio.infographic import render_pdp, render_bab
    from creative_intelligence.adstudio.store import core_dir
    d = request.get_json(force=True, silent=True) or {}
    kind = d.get("kind")
    if kind not in ("pdp", "bab"):
        return jsonify({"error": "kind must be pdp or bab"}), 400
    info = d.get("info") or {}
    conn = _db()
    if _product_dict(conn, pid) is None:
        conn.close(); return jsonify({"error": "product not found"}), 404
    with conn:
        conn.execute("UPDATE adstudio_products SET info_json=? WHERE id=?", (json.dumps(info), pid))

    thumb_path = None
    src, _, sid = (d.get("thumb") or "").partition(":")
    if src == "core" and sid.isdigit():
        r = conn.execute("SELECT image_path FROM adstudio_core WHERE id=?", (int(sid),)).fetchone()
        thumb_path = r["image_path"] if r else None
    elif src == "photo" and sid.isdigit():
        r = conn.execute("SELECT path FROM adstudio_photos WHERE id=?", (int(sid),)).fetchone()
        thumb_path = r["path"] if r else None

    cur = None
    with conn:
        cur = conn.execute("INSERT INTO adstudio_core (product_id, kind, status) VALUES (?,?, 'queued')",
                           (pid, f"info_{kind}"))
    cid = cur.lastrowid
    out = str(core_dir() / f"info_{kind}_{cid}_p{pid}.png")
    name = info.get("name") or ""
    estimate = info.get("estimate", "pieces")
    try:
        if kind == "pdp":
            rows = [r for r in info.get("rows", []) if r.get("on", True)]
            render_pdp(name, rows, out, thumb_path=thumb_path, estimate=estimate)
        else:
            render_bab(name, info.get("bab") or {}, out, thumb_path=thumb_path, estimate=estimate)
    except Exception as exc:
        with conn:
            conn.execute("DELETE FROM adstudio_core WHERE id=?", (cid,))
        conn.close()
        return jsonify({"error": str(exc)[:300]}), 400
    with conn:
        conn.execute("UPDATE adstudio_core SET status='done', image_path=? WHERE id=?", (out, cid))
    conn.close()
    return jsonify({"id": cid})


@app.get("/api/adstudio/core")
def adstudio_core_list() -> Any:
    pid = request.args.get("product_id")
    conn = _db()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, kind, variant, status, is_favorite, error_message, created_at, brief, fix,"
        " image_path IS NOT NULL AS has_image FROM adstudio_core WHERE product_id=?"
        " ORDER BY id DESC LIMIT 400", (int(pid or 0),)).fetchall()]
    conn.close()
    return jsonify({"items": rows})


@app.get("/api/adstudio/core/<int:cid>/image")
def adstudio_core_image(cid: int) -> Any:
    conn = _db()
    row = conn.execute("SELECT image_path FROM adstudio_core WHERE id=?", (cid,)).fetchone()
    conn.close()
    if row is None or not row["image_path"] or not Path(row["image_path"]).exists():
        return ("", 404)
    return send_file(row["image_path"], conditional=True)


@app.post("/api/adstudio/core/<int:cid>/favorite")
def adstudio_core_fav(cid: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("UPDATE adstudio_core SET is_favorite=1-is_favorite WHERE id=?", (cid,))
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/adstudio/core/<int:cid>/delete")
def adstudio_core_delete(cid: int) -> Any:
    conn = _db()
    with conn:
        conn.execute("DELETE FROM adstudio_core WHERE id=?", (cid,))
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/adstudio/export-core")
def adstudio_export_core() -> Any:
    """Zip a product's core set: favorites if any, else everything done."""
    import io, re as _re, zipfile
    pid = int(request.args.get("product_id") or 0)
    conn = _db()
    prod = _product_dict(conn, pid) or {"name": "product"}
    rows = conn.execute("SELECT id, kind, is_favorite, image_path FROM adstudio_core"
                        " WHERE product_id=? AND image_path IS NOT NULL ORDER BY kind, id",
                        (pid,)).fetchall()
    conn.close()
    if any(r["is_favorite"] for r in rows):
        rows = [r for r in rows if r["is_favorite"]]
    slug = _re.sub(r"[^a-z0-9]+", "-", prod["name"].lower()).strip("-") or "product"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            if Path(r["image_path"]).exists():
                z.write(r["image_path"], arcname=f"{slug}_{r['kind']}_{r['id']}.png")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{slug}_core.zip")


if __name__ == "__main__":
    _startup_cleanup()
    _startup_backfill_product_images()
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
