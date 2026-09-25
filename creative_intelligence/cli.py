"""
Creative Intelligence CLI.

Entry point for all creative intelligence operations.
All commands are safe by default (read-only or dry-run).

Usage:
  python -m creative_intelligence.cli <command> [options]

Commands:
  init                  Initialise the database
  ingest                Ingest creatives + performance from raw data files
  tag                   Tag all un-tagged creatives (rule-based)
  tag --ai              Also run AI tagging on unknowns (requires OPENAI_API_KEY)
  extract-patterns      Extract winning creative patterns from tagged data
  score                 Score all creatives
  analyze-visuals       Analyze visual assets for top creatives
  generate-hooks        Generate hook variants from winning patterns
  generate-brief        Generate a UGC or static brief
  generate-test-matrix  Generate a creative test matrix
  load-products         Load product knowledge from files
  report                Generate the operator-facing creative report
  validate              Run all QA/integrity checks
  qa-report             Full QA report with tag distribution + score distribution
  inspect-tag           Show sample creatives for a given tag type/value
  status                Show system status

Run any command with --help for details.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _init(args: argparse.Namespace) -> None:
    from creative_intelligence.db import init_db, get_db_path
    init_db()
    print(f"Database initialised at: {get_db_path()}")


def _ingest(args: argparse.Namespace) -> None:
    from creative_intelligence.db import init_db, get_connection
    from creative_intelligence.ingest.ads_reader import build_creative_records
    from creative_intelligence.ingest.insights_reader import parse_insights
    from creative_intelligence.ingest.creative_fetcher import enrich_records_with_copy
    from datetime import datetime

    init_db()
    conn = get_connection()

    print("Loading creative records from data/raw/...")
    records = build_creative_records()
    print(f"  Found {len(records)} ads")

    if not args.dry_run and not args.skip_copy_fetch:
        print("  Fetching creative copy from Meta API...")
        records = enrich_records_with_copy(records, dry_run=False)
    else:
        print("  Skipping copy fetch (dry-run or --skip-copy-fetch)")

    now = datetime.utcnow().isoformat()
    inserted = updated = 0
    with conn:
        for rec in records:
            existing = conn.execute(
                "SELECT id FROM creatives WHERE id = ?", (rec["creative_id"],)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE creatives SET ad_name=?, campaign_name=?, status=?,
                       format=?, hook_text=?, primary_text=?, headline=?,
                       description=?, cta=?, destination_url=?, last_seen_date=?, updated_at=?
                       WHERE id=?""",
                    (rec["ad_name"], rec["campaign_name"], rec["status"],
                     rec["format"], rec["hook_text"], rec["primary_text"],
                     rec["headline"], rec["description"], rec["cta"],
                     rec["destination_url"], now[:10], now, rec["creative_id"]),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO creatives
                       (id, ad_id, ad_name, adset_id, adset_name, campaign_id, campaign_name,
                        status, format, hook_text, primary_text, headline, description,
                        cta, destination_url, first_seen_date, last_seen_date, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rec["creative_id"], rec["ad_id"], rec["ad_name"],
                     rec["adset_id"], rec["adset_name"], rec["campaign_id"],
                     rec["campaign_name"], rec["status"], rec["format"],
                     rec["hook_text"], rec["primary_text"], rec["headline"],
                     rec["description"], rec["cta"], rec["destination_url"],
                     now[:10], now[:10], now, now),
                )
                inserted += 1
    print(f"  Creatives: {inserted} inserted, {updated} updated")

    # Ingest performance data.
    # Build ad_id → creative_id lookup from the creatives table.
    ad_to_creative = {
        r["ad_id"]: r["id"]
        for r in conn.execute("SELECT id, ad_id FROM creatives").fetchall()
        if r["ad_id"]
    }

    from creative_intelligence.ingest.insights_reader import _PREFIX_MAP, _latest_file
    ranges = ([r for r in _PREFIX_MAP if r in ("yesterday", "7d", "30d") or _latest_file(_PREFIX_MAP[r])]
              if not args.date_range else [args.date_range])
    for dr in ranges:
        print(f"  Loading {dr} performance data...")
        perf_rows = parse_insights(dr)
        perf_inserted = perf_skipped = perf_errors = 0
        with conn:
            for p in perf_rows:
                # Resolve actual creative_id from the ad_id.
                ad_id = p["ad_id"]
                creative_id = ad_to_creative.get(ad_id)
                if not creative_id:
                    perf_skipped += 1
                    continue
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO creative_performance
                           (creative_id, ad_id, date_range, snapshot_date,
                            spend, impressions, clicks, ctr, cpc, cpm, frequency,
                            purchases, revenue, roas, cpa,
                            video_views, video_view_rate)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (creative_id, ad_id, p["date_range"],
                         p["snapshot_date"], p.get("spend"), p.get("impressions"),
                         p.get("clicks"), p.get("ctr"), p.get("cpc"), p.get("cpm"),
                         p.get("frequency"), p.get("purchases"), p.get("revenue"),
                         p.get("roas"), p.get("cpa"),
                         p.get("video_views"), p.get("video_view_rate")),
                    )
                    perf_inserted += 1
                except Exception as e:
                    perf_errors += 1
                    if perf_errors <= 3:
                        print(f"    [WARN] perf insert error for ad {ad_id}: {e}")
        msg = f"    {dr}: {perf_inserted} rows inserted"
        if perf_skipped:
            msg += f" | {perf_skipped} skipped (ad not in creatives table)"
        if perf_errors:
            msg += f" | {perf_errors} errors"
        print(msg)

    conn.close()
    print("Ingest complete.")


def _tag(args: argparse.Namespace) -> None:
    from creative_intelligence.db import get_connection
    from creative_intelligence.tagging.rule_tagger import tag_all
    from creative_intelligence import config

    conn = get_connection()
    records = [dict(r) for r in conn.execute("SELECT * FROM creatives").fetchall()]
    print(f"Tagging {len(records)} creatives (rule-based)...")

    tag_rows = tag_all(records)
    inserted = 0
    with conn:
        for t in tag_rows:
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO creative_tags
                       (creative_id, tag_type, tag_value, confidence, source)
                       VALUES (?,?,?,?,?)""",
                    (t["creative_id"], t["tag_type"], t["tag_value"],
                     t["confidence"], t["source"]),
                )
                inserted += 1
            except Exception as e:
                if inserted == 0 and len(tag_rows) > 0:
                    print(f"  [WARN] First tag insert error: {e}")
                    print(f"         creative_id={t['creative_id']!r}")
    print(f"  {inserted} tags written")

    if args.ai and config.AI_TAGGING_ENABLED:
        from creative_intelligence.tagging.ai_tagger import tag_all_ai
        existing_by_cid: dict = {}
        for t in tag_rows:
            existing_by_cid.setdefault(t["creative_id"], {})[t["tag_type"]] = t["tag_value"]

        print("Running AI tagging on unknowns...")
        ai_tags = tag_all_ai(records, existing_by_cid)
        ai_inserted = 0
        with conn:
            for t in ai_tags:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO creative_tags
                           (creative_id, tag_type, tag_value, confidence, source)
                           VALUES (?,?,?,?,?)""",
                        (t["creative_id"], t["tag_type"], t["tag_value"],
                         t["confidence"], t["source"]),
                    )
                    ai_inserted += 1
                except Exception:
                    pass
        print(f"  {ai_inserted} AI tags written")
    elif args.ai:
        print("  AI tagging skipped (set CI_AI_TAGGING=1 to enable)")

    conn.close()


def _extract_patterns(args: argparse.Namespace) -> None:
    from creative_intelligence.analysis.patterns import extract_patterns, extract_weighted_patterns
    print(f"Extracting {'spend-weighted ' if args.weighted else ''}patterns from {args.date_range} data...")
    pats = (extract_weighted_patterns(date_range=args.date_range) if args.weighted
            else extract_patterns(date_range=args.date_range))
    print(f"  {len(pats)} patterns written")
    for p in pats[:5]:
        print(f"  - {p['pattern_name']} ({p['winner_count']} winners)")


def _score(args: argparse.Namespace) -> None:
    from creative_intelligence.scoring.scorer import score_all_creatives
    print(f"Scoring creatives ({args.date_range})...")
    results = score_all_creatives(date_range=args.date_range)
    if results:
        avg = sum(r["overall"] for r in results if "overall" in r) / len(results)
        print(f"  {len(results)} creatives scored. Average overall score: {avg:.1f}/100")
    else:
        print("  No creatives scored (insufficient data).")


def _analyze_visuals(args: argparse.Namespace) -> None:
    from creative_intelligence.db import get_connection
    from creative_intelligence.vision.image_fetcher import (
        get_creatives_missing_visual_analysis,
        resolve_asset_urls_from_api,
    )
    from creative_intelligence.vision.image_analyzer import analyze_creatives_batch

    conn = get_connection()
    print(f"Finding top {args.limit} creatives without visual analysis...")
    recs = get_creatives_missing_visual_analysis(
        limit=args.limit,
        date_range=args.date_range,
        conn=conn,
    )
    print(f"  Found {len(recs)}")

    if not recs:
        print("  All creatives already analyzed.")
        return

    cids = [r["creative_id"] for r in recs]
    url_map = resolve_asset_urls_from_api(cids, dry_run=args.dry_run)

    summary = analyze_creatives_batch(
        recs, url_map,
        dry_run=args.dry_run,
        provider=args.provider or None,
        conn=conn,
    )
    conn.close()

    status = "[DRY RUN] " if args.dry_run else ""
    print(f"  {status}Processed: {summary['processed']} | Failed: {summary['failed']} | Skipped: {summary['skipped']}")
    if args.dry_run:
        print("  Add --no-dry-run to persist results.")


def _generate_hooks(args: argparse.Namespace) -> None:
    from creative_intelligence.generation.hook_generator import generate_hook_bank

    print(f"Generating hook bank (dry_run={args.dry_run}, diversity={args.diversity}, tone={args.tone})...")
    result = generate_hook_bank(
        date_range           = args.date_range,
        hooks_per_pattern    = args.count,
        max_patterns         = args.max_patterns,
        product_id           = args.product_id or None,
        dry_run              = args.dry_run,
        pattern_id           = int(args.pattern_id) if args.pattern_id else None,
        similarity_threshold = args.similarity_threshold,
        diversity            = args.diversity,
        tone                 = args.tone,
    )
    print(f"  Patterns used: {result['patterns_used']} | Total hooks: {result['total_hooks']}")
    for r in result.get("results", []):
        print(f"\n  Pattern: {r.get('pattern_name', '')}")
        qa = r.get("qa_summary", {})
        if qa:
            total  = qa.get("total", 0)
            passed = qa.get("passed", 0)
            rate   = qa.get("pass_rate", 0)
            print(f"  QA: {passed}/{total} passed ({rate:.0%})")
            fc = qa.get("flag_counts", {})
            if fc:
                flag_str = ", ".join(f"{k}={v}" for k, v in fc.items())
                print(f"  Flags: {flag_str}")
        for hook in r.get("hooks", []):
            print(f"    - {hook}")
    if args.dry_run:
        print("\n  Add --no-dry-run to persist to DB.")


def _generate_brief(args: argparse.Namespace) -> None:
    from creative_intelligence.generation.brief_generator import (
        generate_ugc_brief, generate_static_brief,
    )
    btype = args.type or "ugc"
    print(f"Generating {btype} brief (dry_run={args.dry_run})...")

    if btype == "ugc":
        result = generate_ugc_brief(
            pattern_id = int(args.pattern_id) if args.pattern_id else None,
            hook_text  = args.hook or "",
            product_id = args.product_id or None,
            dry_run    = args.dry_run,
        )
    else:
        result = generate_static_brief(
            pattern_id = int(args.pattern_id) if args.pattern_id else None,
            headline   = args.hook or "",
            product_id = args.product_id or None,
            dry_run    = args.dry_run,
        )

    brief = result.get("brief", {})
    print(json.dumps(brief, indent=2))
    if not args.dry_run and result.get("script_id"):
        print(f"\n  Saved as script_id={result['script_id']}")


def _generate_test_matrix(args: argparse.Namespace) -> None:
    from creative_intelligence.generation.brief_generator import generate_test_matrix
    print(f"Generating test matrix (dry_run={args.dry_run})...")
    result = generate_test_matrix(
        pattern_id = int(args.pattern_id) if args.pattern_id else None,
        product_id = args.product_id or None,
        dry_run    = args.dry_run,
    )
    print(json.dumps(result.get("matrix", {}), indent=2))


def _load_products(args: argparse.Namespace) -> None:
    from creative_intelligence.db import init_db
    from creative_intelligence.product_knowledge.loader import ingest_directory
    init_db()
    directory = Path(args.directory) if args.directory else None
    print(f"Loading product knowledge from {directory or 'default KB dir'}...")
    summary = ingest_directory(directory)
    print(f"  JSON products: {summary['json']}")
    print(f"  CSV products:  {summary['csv']}")
    print(f"  KB documents:  {len(summary['docs'])}")
    if summary["skipped"]:
        print(f"  Skipped:       {summary['skipped']}")

    print("Linking creatives to products...")
    from creative_intelligence.product_knowledge.enricher import link_creatives_to_products
    n = link_creatives_to_products()
    print(f"  {n} creatives linked to products")


def _report(args: argparse.Namespace) -> None:
    from creative_intelligence.reports.creative_report import write_report
    print(f"Generating creative report ({args.date_range})...")
    path = write_report(date_range=args.date_range)
    print(f"  Report written to: {path}")


def _validate(args: argparse.Namespace) -> None:
    from creative_intelligence.validation.qa_checks import run_all_checks

    print(f"Running QA checks ({args.date_range})...\n")
    results = run_all_checks(date_range=args.date_range)

    icons = {"ok": "✓", "warn": "⚠", "fail": "✗"}
    for r in results:
        icon = icons.get(r["status"], "?")
        print(f"  [{icon}] {r['name']:<45}  {r['detail']}")

    fails = [r for r in results if r["status"] == "fail"]
    warns = [r for r in results if r["status"] == "warn"]
    oks   = [r for r in results if r["status"] == "ok"]
    print(f"\n  {len(oks)} ok  |  {len(warns)} warnings  |  {len(fails)} failures")

    if fails:
        print("\n  Failures must be resolved before trusting analysis output.")
    elif warns:
        print("\n  Warnings are advisory — review before generating content.")
    else:
        print("\n  All checks passed. System is ready for analysis.")


def _qa_report(args: argparse.Namespace) -> None:
    """Full QA report: tag distribution, score distribution, top unknowns."""
    from creative_intelligence.validation.qa_checks import (
        check_tag_distribution,
        check_unknown_tag_rate,
        check_score_distribution,
        check_copy_field_coverage,
        get_unknown_sample,
    )
    from creative_intelligence.db import get_connection

    conn = get_connection()

    print("=" * 60)
    print("QA REPORT — CREATIVE INTELLIGENCE SUBSYSTEM")
    print("=" * 60)

    # Copy field coverage
    print("\n## Copy Field Coverage")
    cov = check_copy_field_coverage(conn)
    print(f"  {cov['detail']}")

    # Tag distribution
    print("\n## Tag Distribution")
    dist = check_tag_distribution(conn)
    current_type = None
    for r in dist["rows"]:
        if r["tag_type"] != current_type:
            current_type = r["tag_type"]
            print(f"\n  {current_type}:")
        bar = "█" * int(r["pct"] / 5)
        print(f"    {r['tag_value']:<25} {r['count']:>5} ({r['pct']:>5.1f}%)  {bar}")

    # Unknown rates
    print("\n## Unknown Tag Rate by Dimension")
    unk = check_unknown_tag_rate(conn)
    for r in unk["rows"]:
        flag = " ← HIGH" if r["unknown_pct"] > 40 else ""
        print(f"  {r['tag_type']:<25} unknown: {r['unknown_count']:>4} / {r['total']:>4} ({r['unknown_pct']:>5.1f}%){flag}")

    # Show sample unknowns for highest-unknown dimensions
    high_unk = [r for r in unk["rows"] if r["unknown_pct"] > 40]
    if high_unk:
        print("\n## Sample Unknowns (for rule calibration)")
        for r in high_unk[:3]:
            dim = r["tag_type"]
            samples = get_unknown_sample(dim, limit=3, conn=conn)
            print(f"\n  Dimension: {dim}")
            for s in samples:
                hook    = (s.get("hook_text") or "")[:80] or "(no hook)"
                primary = (s.get("primary_text") or "")[:80] or "(no primary text)"
                print(f"    Ad: {(s.get('ad_name') or '')[:40]}")
                print(f"    Hook: {hook}")
                print(f"    Primary: {primary}")

    # Score distribution
    print("\n## Score Distribution")
    score_check = check_score_distribution(conn)
    print(f"  {score_check['detail']}")

    if score_check["rows"]:
        row = score_check["rows"][0]
        buckets = conn.execute(
            """SELECT
                   CASE
                     WHEN score < 20 THEN '0-19'
                     WHEN score < 40 THEN '20-39'
                     WHEN score < 60 THEN '40-59'
                     WHEN score < 80 THEN '60-79'
                     ELSE '80-100'
                   END AS bucket,
                   COUNT(*) AS n
               FROM creative_scores WHERE score_type = 'overall'
               GROUP BY bucket ORDER BY bucket"""
        ).fetchall()
        print("\n  Score buckets (overall):")
        for b in buckets:
            bar = "█" * int(b["n"])
            print(f"    {b['bucket']:>7}  {b['n']:>4}  {bar}")

    conn.close()
    print("\n" + "=" * 60)


def _inspect_tag(args: argparse.Namespace) -> None:
    """Show sample creatives for a given tag dimension/value."""
    from creative_intelligence.validation.qa_checks import get_sample_by_tag

    tag_type  = args.tag_type
    tag_value = args.tag_value or "unknown"
    print(f"Sample creatives for {tag_type}={tag_value}:\n")

    samples = get_sample_by_tag(tag_type, tag_value, limit=args.limit)
    if not samples:
        print("  No creatives found with this tag.")
        return

    for i, s in enumerate(samples, 1):
        print(f"  [{i}] {s.get('ad_name', 'unnamed')}")
        print(f"       hook:    {(s.get('hook_text') or '(empty)')[:100]}")
        print(f"       primary: {(s.get('primary_text') or '(empty)')[:100]}")
        spend = s.get("spend")
        ctr   = s.get("ctr")
        roas  = s.get("roas")
        metrics = []
        if spend:  metrics.append(f"spend=${spend:.0f}")
        if ctr:    metrics.append(f"CTR={ctr:.2%}")
        if roas:   metrics.append(f"ROAS={roas:.2f}x")
        if metrics:
            print(f"       metrics: {' | '.join(metrics)}")
        print()


def _sync_shopify(args: argparse.Namespace) -> None:
    """Fetch products from Shopify and optionally sync into the products DB table."""
    from creative_intelligence import config as cfg

    if not cfg.SHOPIFY_ENABLED:
        print("ERROR: Shopify integration is not enabled.")
        print("Set CI_SHOPIFY_ENABLED=1 and configure SHOPIFY_STORE_URL + SHOPIFY_ACCESS_TOKEN.")
        return

    from creative_intelligence.product_knowledge.shopify_adapter import run_sync

    dry = args.dry_run
    mode = "DRY RUN — fetching only, no DB writes" if dry else "LIVE SYNC — will write to DB"
    print(f"sync-shopify  ({mode})")
    print(f"Store: {cfg.SHOPIFY_STORE_URL}")
    print(f"API version: {cfg.SHOPIFY_API_VERSION}")
    if not dry:
        confirm = input("Confirm sync to DB? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    result = run_sync(dry_run=dry, fetch_metafields=args.metafields)

    pkb = result["pkb"]
    print(f"\nFetched {result['product_count']} active products from Shopify:\n")
    for pid, entry in pkb.items():
        p = entry["product"]
        benefits_n = len(entry.get("benefits", []))
        offers_n   = len(entry.get("offers", []))
        print(f"  {p['name']:<40}  price=${p.get('price', 0):.2f}"
              f"  benefits={benefits_n}  offers={offers_n}  id={pid}")

    if not dry:
        print(f"\nSynced {len(result['synced_ids'])} products to DB.")
        for pid in result["synced_ids"]:
            print(f"  ✓ {pid}")
    else:
        print(f"\n[dry-run] No changes written. Pass --no-dry-run to persist.")


def _status(args: argparse.Namespace) -> None:
    from creative_intelligence.db import db_exists, get_db_path, get_connection

    print("=== Creative Intelligence System Status ===\n")
    db_path = get_db_path()
    print(f"DB path:   {db_path}")
    print(f"DB exists: {db_exists()}")

    if not db_exists():
        print("\nRun `init` first.")
        return

    conn = get_connection()
    for table in [
        "creatives", "creative_performance", "creative_tags",
        "creative_patterns", "products", "product_benefits",
        "generated_hooks", "generated_scripts", "creative_visual_attributes",
    ]:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:<40} {n:>6} rows")
        except Exception as e:
            print(f"  {table:<40} ERROR: {e}")
    conn.close()


# ─────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m creative_intelligence.cli",
        description="Creative Intelligence CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialise the database")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest creatives and performance data")
    p_ingest.add_argument("--dry-run", action="store_true", default=False)
    p_ingest.add_argument("--skip-copy-fetch", action="store_true", default=False)
    p_ingest.add_argument("--date-range", default=None, choices=["yesterday", "7d", "30d", "90d"])

    # tag
    p_tag = sub.add_parser("tag", help="Tag creatives")
    p_tag.add_argument("--ai", action="store_true", default=False, help="Also run AI tagging")

    # extract-patterns
    p_pat = sub.add_parser("extract-patterns", help="Extract winning patterns")
    p_pat.add_argument("--date-range", default="7d")
    p_pat.add_argument("--weighted", action="store_true", default=False,
                       help="Spend-weighted patterns (pooled ROAS vs account average)")

    # score
    p_score = sub.add_parser("score", help="Score all creatives")
    p_score.add_argument("--date-range", default="7d")

    # analyze-visuals
    p_vis = sub.add_parser("analyze-visuals", help="Analyze visual assets")
    p_vis.add_argument("--dry-run", action="store_true", default=True)
    p_vis.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    p_vis.add_argument("--limit", type=int, default=20)
    p_vis.add_argument("--date-range", default="7d")
    p_vis.add_argument("--provider", default=None, choices=["openai", "mock"])

    # generate-hooks
    p_hooks = sub.add_parser("generate-hooks", help="Generate hook bank")
    p_hooks.add_argument("--dry-run", action="store_true", default=True)
    p_hooks.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    p_hooks.add_argument("--count", type=int, default=5, help="Hooks per pattern")
    p_hooks.add_argument("--max-patterns", type=int, default=5)
    p_hooks.add_argument("--date-range", default="7d")
    p_hooks.add_argument("--product-id", default=None)
    p_hooks.add_argument("--pattern-id", default=None, help="Generate from a specific pattern ID only")
    p_hooks.add_argument(
        "--similarity-threshold", type=float, default=0.65, metavar="FLOAT",
        help="Reject hooks above this Jaccard similarity to source examples (default 0.65)",
    )
    p_hooks.add_argument(
        "--diversity", choices=["low", "medium", "high"], default="medium",
        help="Structural diversity enforcement (default medium)",
    )
    p_hooks.add_argument(
        "--tone", choices=["authentic", "punchy", "emotional", "educational"], default="authentic",
        help="Copywriting tone (default authentic)",
    )

    # generate-brief
    p_brief = sub.add_parser("generate-brief", help="Generate a creative brief")
    p_brief.add_argument("--type", choices=["ugc", "static"], default="ugc")
    p_brief.add_argument("--dry-run", action="store_true", default=True)
    p_brief.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    p_brief.add_argument("--pattern-id", default=None)
    p_brief.add_argument("--product-id", default=None)
    p_brief.add_argument("--hook", default="", help="Opening hook text")

    # generate-test-matrix
    p_matrix = sub.add_parser("generate-test-matrix", help="Generate a test matrix")
    p_matrix.add_argument("--dry-run", action="store_true", default=True)
    p_matrix.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    p_matrix.add_argument("--pattern-id", default=None)
    p_matrix.add_argument("--product-id", default=None)

    # load-products
    p_prod = sub.add_parser("load-products", help="Load product knowledge")
    p_prod.add_argument("--directory", default=None, help="Path to product KB directory")

    # report
    p_report = sub.add_parser("report", help="Generate creative report")
    p_report.add_argument("--date-range", default="7d")

    # validate
    p_val = sub.add_parser("validate", help="Run all QA/integrity checks")
    p_val.add_argument("--date-range", default="7d")

    # qa-report
    p_qa = sub.add_parser("qa-report", help="Full QA report: tag distribution, unknowns, scores")
    p_qa.add_argument("--date-range", default="7d")

    # inspect-tag
    p_inspect = sub.add_parser("inspect-tag", help="Show sample creatives for a tag value")
    p_inspect.add_argument("tag_type",  help="e.g. hook_type, angle, format")
    p_inspect.add_argument("tag_value", nargs="?", default="unknown",
                            help="Tag value to inspect (default: unknown)")
    p_inspect.add_argument("--limit", type=int, default=5)

    # sync-shopify
    p_shopify = sub.add_parser("sync-shopify", help="Sync Shopify product catalog into DB")
    p_shopify.add_argument("--dry-run", action="store_true", default=True,
                            help="Fetch and map only; do not write to DB (default)")
    p_shopify.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                            help="Write synced products to DB")
    p_shopify.add_argument("--metafields", action="store_true", default=False,
                            help="Also fetch Shopify metafields per product (extra API calls)")

    # status
    sub.add_parser("status", help="Show system status")

    args = parser.parse_args()

    dispatch = {
        "init":                _init,
        "ingest":              _ingest,
        "tag":                 _tag,
        "extract-patterns":    _extract_patterns,
        "score":               _score,
        "analyze-visuals":     _analyze_visuals,
        "generate-hooks":      _generate_hooks,
        "generate-brief":      _generate_brief,
        "generate-test-matrix": _generate_test_matrix,
        "load-products":       _load_products,
        "report":              _report,
        "validate":            _validate,
        "qa-report":           _qa_report,
        "inspect-tag":         _inspect_tag,
        "sync-shopify":        _sync_shopify,
        "status":              _status,
    }

    handler = dispatch.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}")
            raise
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
