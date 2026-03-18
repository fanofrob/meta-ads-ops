"""
check_landing_pages.py

Fetches destination URLs for active ads via the Meta Graph API (read-only),
checks each URL for health issues, and saves results to
outputs/url_health/YYYY-MM-DD.json.

Checks performed:
  - HTTP status code and final URL after redirects
  - Redirect count and loop detection
  - Response time
  - UTM parameter presence
  - Domain mismatch between original and final URL
  - Shopify sold-out / out-of-stock signals (if applicable)

Run:
    python src/check_landing_pages.py            # live checks
    python src/check_landing_pages.py --dry-run  # fetch URLs only, skip HTTP checks

All Meta API calls are read-only GET requests.
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from api import load_env, _get

load_dotenv()

RAW_DIR = Path("data/raw")
OUT_DIR = Path("outputs/url_health")
API_LOG = Path("outputs/api.log")

# URL checking thresholds
_TIMEOUT = 10            # seconds per request
_MAX_REDIRECTS = 10
_SLOW_THRESHOLD = 3.0    # seconds — pages above this are flagged slow
_REQUEST_DELAY = 0.15    # polite delay between destination requests (seconds)
_CREATIVE_BATCH = 50     # creative IDs per batch API call

# Note: UTM parameters are NOT checked here. Meta Ads appends UTMs dynamically
# at click time via the ad's "URL Parameters" field — the destination URL stored
# in the creative is always the clean product URL and will never contain UTMs.

# Shopify / e-commerce out-of-stock signals (case-insensitive)
_OOS_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r'\bsold[\s\-]?out\b',
    r'\bout[\s\-]?of[\s\-]?stock\b',
    r'"available"\s*:\s*false',
    r'"inventory_quantity"\s*:\s*0',
    r'data-product-available\s*=\s*["\']false["\']',
    r'class=["\'][^"\']*\bsold-?out\b[^"\']*["\']',
    r'<button[^>]*disabled[^>]*>[^<]*add\s+to\s+cart',
    r'data-add-to-cart[^>]*disabled',
]]

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ---------------------------------------------------------------------------
# API call logging
# ---------------------------------------------------------------------------

def _log_api(endpoint: str, status: str = "OK") -> None:
    API_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with API_LOG.open("a") as f:
        f.write(f"{ts} | GET | {endpoint} | {status}\n")


# ---------------------------------------------------------------------------
# Creative URL fetching
# ---------------------------------------------------------------------------

_CREATIVE_FIELDS = "link_url,object_url,object_story_spec"

GRAPH_BASE = "https://graph.facebook.com"


def _fetch_creatives_batch(creative_ids: list, env: dict) -> dict:
    """
    Fetch creative details for up to _CREATIVE_BATCH IDs at once.
    Returns dict of {creative_id: creative_object}.
    Uses its own request logic to handle empty/non-JSON responses gracefully.
    """
    version = env["META_API_VERSION"]
    url = f"{GRAPH_BASE}/{version}"
    params = {
        "ids": ",".join(creative_ids),
        "fields": _CREATIVE_FIELDS,
        "access_token": env["META_ACCESS_TOKEN"],
    }
    endpoint_label = f"{GRAPH_BASE}/{version}?ids=<{len(creative_ids)} creatives>&fields={_CREATIVE_FIELDS}"

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=60)
        except requests.exceptions.RequestException as exc:
            print(f"  [WARN] Creative batch request failed: {exc.__class__.__name__} — skipping batch")
            _log_api(endpoint_label, f"ERROR:{exc.__class__.__name__}")
            return {}

        if not resp.text or not resp.text.strip():
            wait = 2 ** attempt
            print(f"  [WARN] Empty response for creative batch — retrying in {wait}s...")
            time.sleep(wait)
            continue

        try:
            data = resp.json()
        except ValueError:
            wait = 2 ** attempt
            print(f"  [WARN] Non-JSON response for creative batch — retrying in {wait}s...")
            time.sleep(wait)
            continue

        if "error" in data:
            err = data["error"]
            print(f"  [WARN] API error on creative batch (code {err.get('code')}): {err.get('message')} — skipping")
            _log_api(endpoint_label, f"API_ERROR:{err.get('code')}")
            return {}

        _log_api(endpoint_label, "OK")
        return data

    print(f"  [WARN] Creative batch failed after retries — skipping {len(creative_ids)} creatives")
    _log_api(endpoint_label, "ERROR:retries_exhausted")
    return {}


def fetch_all_creative_urls(ads: list, env: dict) -> dict:
    """
    Returns dict mapping {creative_id: url_or_None} for all creative IDs found
    in the provided ads list. Batches API calls.
    """
    # Collect unique creative IDs
    unique_ids = list({a["creative"]["id"] for a in ads if a.get("creative", {}).get("id")})
    print(f"  Fetching creative details for {len(unique_ids)} unique creatives "
          f"({len(unique_ids) // _CREATIVE_BATCH + 1} batches)...")

    creative_map: dict = {}
    for i in range(0, len(unique_ids), _CREATIVE_BATCH):
        batch = unique_ids[i: i + _CREATIVE_BATCH]
        result = _fetch_creatives_batch(batch, env)
        for cid, creative in result.items():
            url = _extract_url(creative)
            creative_map[cid] = url
        if i + _CREATIVE_BATCH < len(unique_ids):
            time.sleep(0.3)  # brief pause between batches

    found = sum(1 for v in creative_map.values() if v)
    print(f"  Extracted URLs: {found} of {len(unique_ids)} creatives had a destination URL.")
    return creative_map


def _extract_url(creative: dict) -> Optional[str]:
    """
    Extract the primary destination URL from a Meta creative object.
    Tries multiple field paths in priority order.
    """
    if not creative:
        return None

    # Direct fields
    for field in ("link_url", "object_url"):
        val = creative.get(field)
        if val and val.startswith("http"):
            return val

    # object_story_spec variants
    spec = creative.get("object_story_spec") or {}

    # Link / carousel ad
    link_data = spec.get("link_data") or {}
    if link_data.get("link", "").startswith("http"):
        return link_data["link"]
    # Link ad CTA override
    cta_link = (link_data.get("call_to_action") or {}).get("value", {}).get("link", "")
    if cta_link.startswith("http"):
        return cta_link

    # Video ad
    video_data = spec.get("video_data") or {}
    v_cta_link = (video_data.get("call_to_action") or {}).get("value", {}).get("link", "")
    if v_cta_link.startswith("http"):
        return v_cta_link

    # DPA / template
    template_data = spec.get("template_data") or {}
    if template_data.get("link", "").startswith("http"):
        return template_data["link"]

    return None


# ---------------------------------------------------------------------------
# URL health checking
# ---------------------------------------------------------------------------


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _check_oos(text: str) -> bool:
    """Check first 50KB of HTML for out-of-stock signals."""
    sample = text[:50_000]
    return any(pat.search(sample) for pat in _OOS_PATTERNS)


def check_url(session: requests.Session, url: str) -> dict:
    """
    Perform a full health check on a single URL.
    Returns a result dict with all findings.
    """
    result: dict = {
        "url": url,
        "final_url": None,
        "status_code": None,
        "redirect_count": 0,
        "response_time": None,
        "is_broken": False,
        "is_slow": False,
        "is_redirect_loop": False,
        "domain_mismatch": False,
        "is_out_of_stock": False,
        "issues": [],
        "severity": "ok",
        "error": None,
    }

    orig_domain = _domain(url)

    try:
        start = time.monotonic()

        # First request (no auto-redirect so we can count and detect loops)
        resp = session.get(
            url,
            timeout=_TIMEOUT,
            allow_redirects=False,
            headers={"User-Agent": _UA},
        )

        # Follow redirect chain manually
        seen_urls = {url}
        current = resp
        redirect_count = 0

        while current.is_redirect and redirect_count < _MAX_REDIRECTS:
            next_url = current.headers.get("Location", "")
            if not next_url:
                break
            # Resolve relative redirects
            if next_url.startswith("/"):
                parsed = urlparse(current.url)
                next_url = f"{parsed.scheme}://{parsed.netloc}{next_url}"

            if next_url in seen_urls:
                result["is_redirect_loop"] = True
                result["issues"].append("Redirect loop detected")
                break
            seen_urls.add(next_url)

            try:
                current = session.get(
                    next_url,
                    timeout=_TIMEOUT,
                    allow_redirects=False,
                    headers={"User-Agent": _UA},
                )
            except Exception:
                break
            redirect_count += 1

        elapsed = time.monotonic() - start
        result["response_time"] = round(elapsed, 2)
        result["redirect_count"] = redirect_count
        result["status_code"] = current.status_code
        result["final_url"] = current.url

        # Domain mismatch
        final_domain = _domain(current.url)
        if orig_domain and final_domain and orig_domain != final_domain:
            result["domain_mismatch"] = True
            result["issues"].append(f"Domain redirect: {orig_domain} → {final_domain}")

        # Broken (4xx / 5xx)
        if current.status_code >= 400:
            result["is_broken"] = True
            result["issues"].append(f"HTTP {current.status_code}")

        # Slow
        if elapsed >= _SLOW_THRESHOLD:
            result["is_slow"] = True
            result["issues"].append(f"Slow response {elapsed:.1f}s")

        # Shopify / e-commerce OOS detection on 200 HTML responses
        if current.status_code == 200 and not result["is_broken"]:
            ct = current.headers.get("Content-Type", "")
            if "html" in ct.lower():
                try:
                    if _check_oos(current.text):
                        result["is_out_of_stock"] = True
                        result["issues"].append("Product appears out of stock or sold out")
                except Exception:
                    pass

    except requests.exceptions.ConnectionError as exc:
        result["is_broken"] = True
        result["error"] = "connection_error"
        result["issues"].append(f"Connection failed: {exc.__class__.__name__}")
    except requests.exceptions.Timeout:
        result["is_slow"] = True
        result["error"] = "timeout"
        result["issues"].append(f"Request timed out after {_TIMEOUT}s")
    except Exception as exc:
        result["is_broken"] = True
        result["error"] = "unexpected_error"
        result["issues"].append(f"Unexpected error: {exc.__class__.__name__}")

    # Severity classification
    if (
        result["is_broken"]
        or result["is_redirect_loop"]
        or result["is_out_of_stock"]
    ):
        result["severity"] = "high"
    elif (
        result["is_slow"]
        or result["domain_mismatch"]
    ):
        result["severity"] = "medium"
    elif result["issues"]:
        result["severity"] = "low"

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_latest(prefix: str) -> list:
    files = sorted(RAW_DIR.glob(f"{prefix}_????-??-??.json"), reverse=True)
    if not files:
        return []
    return json.loads(files[0].read_text())


def _load_insights_spend() -> dict:
    """Return {ad_id: spend_float} from latest daily insights."""
    insights = _load_latest("insights")
    return {
        r["ad_id"]: float(r.get("spend") or 0)
        for r in insights
        if r.get("ad_id")
    }


def _build_ad_id_maps() -> tuple:
    """
    Build lookup dicts from raw data files:
    - ad_to_adset: {ad_id: {"adset_id": ..., "adset_name": ...}}
    - adset_to_campaign: {adset_id: {"campaign_id": ..., "campaign_name": ...}}
    Returns (ad_to_adset, adset_to_campaign).
    """
    ads = _load_latest("ads")
    adsets = _load_latest("adsets")
    campaigns = _load_latest("campaigns")

    camp_by_id = {c["id"]: c.get("name", "") for c in campaigns if c.get("id")}
    adset_to_campaign = {
        a["id"]: {
            "campaign_id": a.get("campaign_id", ""),
            "campaign_name": camp_by_id.get(a.get("campaign_id", ""), ""),
        }
        for a in adsets if a.get("id")
    }
    ad_to_adset = {}
    adset_names = {a["id"]: a.get("name", "") for a in adsets if a.get("id")}
    for ad in ads:
        ad_id = ad.get("id")
        adset_id = ad.get("adset_id", "")
        if ad_id:
            ad_to_adset[ad_id] = {
                "adset_id": adset_id,
                "adset_name": adset_names.get(adset_id, ""),
            }

    return ad_to_adset, adset_to_campaign


def _load_insights_7d_spend() -> dict:
    """Return {ad_id: total_spend_float} summed over the last 7 days from insights_30d."""
    data = _load_latest("insights_30d")
    if not data:
        return {}
    dates = sorted({r.get("date_start") for r in data if r.get("date_start")})
    last_7 = set(dates[-7:]) if len(dates) >= 7 else set(dates)
    totals: dict = {}
    for r in data:
        if r.get("date_start") in last_7:
            ad_id = r.get("ad_id")
            if ad_id:
                totals[ad_id] = totals.get(ad_id, 0.0) + float(r.get("spend") or 0)
    return {k: round(v, 2) for k, v in totals.items()}


def main(dry_run: bool = False) -> None:
    print(f"{'[DRY RUN] ' if dry_run else ''}Checking landing pages...\n")

    env = load_env()

    # Load ads and build parent status maps
    ads = _load_latest("ads")
    adsets = _load_latest("adsets")
    campaigns = _load_latest("campaigns")

    adset_status = {a["id"]: a.get("status") for a in adsets if a.get("id")}
    adset_to_campaign_id = {a["id"]: a.get("campaign_id") for a in adsets if a.get("id")}
    camp_status = {c["id"]: c.get("status") for c in campaigns if c.get("id")}

    # Only check ads that are truly delivering: ad + adset + campaign all ACTIVE
    active = []
    for a in ads:
        if a.get("status") != "ACTIVE" or not a.get("creative", {}).get("id"):
            continue
        asid = a.get("adset_id", "")
        cid = adset_to_campaign_id.get(asid)
        if adset_status.get(asid) == "ACTIVE" and camp_status.get(cid) == "ACTIVE":
            active.append(a)

    print(f"  Truly active ads (ad + adset + campaign all ACTIVE): {len(active)}")

    if not active:
        print("[WARN] No active ads found. Nothing to check.")
        return

    # Load spend per ad for priority context
    spend_by_ad = _load_insights_spend()
    spend_7d_by_ad = _load_insights_7d_spend()
    ad_to_adset, adset_to_campaign = _build_ad_id_maps()

    # Fetch creative details from Meta API
    creative_url_map = fetch_all_creative_urls(active, env)

    # Build list of unique URLs with associated ad context
    # url → {url, ads: [{ad_id, ad_name, campaign_name (unknown here), spend}], total_spend}
    url_to_ads: dict = {}
    skipped_no_url = 0

    for ad in active:
        cid = ad["creative"]["id"]
        url = creative_url_map.get(cid)
        if not url:
            skipped_no_url += 1
            continue

        ad_id = ad["id"]
        spend = spend_by_ad.get(ad_id, 0.0)

        if url not in url_to_ads:
            url_to_ads[url] = {"url": url, "ads": [], "total_spend": 0.0}
        adset_info = ad_to_adset.get(ad_id, {})
        adset_id = adset_info.get("adset_id", "")
        camp_info = adset_to_campaign.get(adset_id, {})
        url_to_ads[url]["ads"].append({
            "ad_id": ad_id,
            "ad_name": ad.get("name", ""),
            "adset_id": adset_id,
            "adset_name": adset_info.get("adset_name", ""),
            "campaign_id": camp_info.get("campaign_id", ""),
            "campaign_name": camp_info.get("campaign_name", ""),
            "spend_yesterday": spend_by_ad.get(ad_id, 0.0),
            "spend_7d": spend_7d_by_ad.get(ad_id, 0.0),
        })
        url_to_ads[url]["total_spend"] = round(url_to_ads[url]["total_spend"] + spend, 2)

    unique_urls = list(url_to_ads.values())
    print(f"\n  Unique destination URLs: {len(unique_urls)}")
    print(f"  Ads skipped (no URL in creative): {skipped_no_url}")

    if not unique_urls:
        print("[WARN] No destination URLs found. Check that creative fields include link data.")
        return

    if dry_run:
        print("\n[DRY RUN] Skipping HTTP checks. URLs that would be checked:")
        for entry in unique_urls[:10]:
            print(f"  {entry['url'][:80]} ({len(entry['ads'])} ads, ${entry['total_spend']:.0f} spend)")
        if len(unique_urls) > 10:
            print(f"  ... and {len(unique_urls) - 10} more")
        return

    # Check each URL
    print(f"\n  Checking {len(unique_urls)} URLs (timeout={_TIMEOUT}s, slow={_SLOW_THRESHOLD}s)...\n")

    session = requests.Session()
    results = []
    counters = {"ok": 0, "high": 0, "medium": 0, "low": 0}

    for i, entry in enumerate(unique_urls, 1):
        url = entry["url"]
        short = url[:70]
        print(f"  [{i}/{len(unique_urls)}] {short}{'...' if len(url) > 70 else ''}", end="", flush=True)

        check = check_url(session, url)
        check["ads"] = entry["ads"]
        check["ad_count"] = len(entry["ads"])
        check["total_spend"] = entry["total_spend"]

        sev = check["severity"]
        counters[sev] = counters.get(sev, 0) + 1

        flag = {"high": " [HIGH]", "medium": " [MED]", "low": " [LOW]"}.get(sev, "")
        status = check.get("status_code") or "ERR"
        rt = f"{check['response_time']}s" if check.get("response_time") is not None else "—"
        print(f" → {status} {rt}{flag}")

        results.append(check)
        time.sleep(_REQUEST_DELAY)

    session.close()

    # Sort by severity (high first), then by spend descending
    _sev_rank = {"high": 0, "medium": 1, "low": 2, "ok": 3}
    results.sort(key=lambda r: (_sev_rank.get(r["severity"], 3), -r.get("total_spend", 0)))

    # Build output
    output = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "report_date": date.today().isoformat(),
        "summary": {
            "total_checked": len(results),
            "broken": counters.get("high", 0),
            "warnings": counters.get("medium", 0),
            "minor": counters.get("low", 0),
            "ok": counters.get("ok", 0),
        },
        "results": results,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(output, indent=2))

    print(f"\n  Summary: {counters}")
    print(f"\n[OK] URL health results saved to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check landing page health for active Meta ads.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and display URLs without making HTTP requests to destination pages.",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
