"""
unit_economics.py

Loads per-product unit economics from a Google Sheet.
Provides change detection against the most recent snapshot.

Cell map (identical layout on every tab):
  B2  → AOV
  B10 → Total COGS per unit
  B12 → Gross margin %
  G5  → 20% below breakeven ROAS (floor)
  G7  → First-purchase breakeven ROAS
  G10 → 20% above breakeven ROAS
  G19 → 28DC ROAS target
  G20 → 7DC ROAS target
  H22 → 7DC CPA target
  B13 → 90-day LTV multiplier
  B14 → 1-year LTV multiplier

One batchGet API call per tab — no per-cell requests.
"""

import json
from datetime import date
from pathlib import Path
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Ordered list so batchGet response indices are stable
_FIELD_ORDER = [
    "aov",
    "cogs_per_unit",
    "margin_pct",
    "roas_floor",
    "breakeven_roas",
    "roas_target_20pct_above",
    "roas_target_28dc",
    "roas_target_7dc",
    "cpa_target_7dc",
    "ltv_90d_multiplier",
    "ltv_1yr_multiplier",
]

_CELL_MAP = {
    "aov":                    "B2",
    "cogs_per_unit":          "B10",
    "margin_pct":             "B11",   # B11 = % cost of product (gross margin proxy)
    "roas_floor":             "E4",    # 20% below breakeven ROAS
    "breakeven_roas":         "E7",    # First-purchase breakeven ROAS
    "roas_target_20pct_above":"E10",   # 20% above breakeven ROAS
    "roas_target_28dc":       "H18",   # 28DC ROAS target
    "roas_target_7dc":        "H19",   # 7DC ROAS target
    "cpa_target_7dc":         "H22",   # 7DC CPA target
    "ltv_90d_multiplier":     "B13",
    "ltv_1yr_multiplier":     "B14",
}

# Keyword → product tab name mapping (lowercase keywords).
# Order matters: longer / more specific phrases MUST come before shorter ones
# that could partially match (e.g. "cherry plum" before "cherry mango" before
# a hypothetical plain "cherry").
_PRODUCT_KEYWORDS = [
    ("Cherry Mango",                    ["cherry mango"]),
    ("Cherry Plum (BC)",                ["cherry plum"]),
    ("Bing Cherry (BC)",                ["bing cherry"]),
    ("Pink Pineapple (BC)",             ["pink pineapple"]),
    ("Rainier Cherry (BC)",             ["rainier cherry", "rainier"]),
    ("Purple Passion Fruit (BC)",       ["purple passion fruit", "passion fruit"]),
    ("Fuyu Persimmon (BC)",             ["fuyu persimmon", "fuyu"]),
    ("Chocolate Persimmon",             ["chocolate persimmon"]),
    ("Mangosteen (BC)",                 ["mangosteen"]),
    ("Honey Mango",                     ["honey mango"]),
    ("Avocado Variety Box (have not run own campaign)", ["avocado variety box"]),
    ("Variety Box",                     ["variety box"]),
    ("Cherimoya",                       ["cherimoya"]),
    ("Lychee",                          ["lychee"]),
    ("Loquat",                          ["loquat"]),
    ("Papaya",                          ["hawaiian papaya", "papaya"]),
    ("Hawaiian Papaya",                 ["hawaiian papaya"]),
    ("Pineapple Guava (needs price per lb)", ["pineapple guava"]),
    ("Cotton Candy Grape (needs price per lb)", ["cotton candy grape"]),
    ("Pomegranate",                     ["pomegranate"]),
    ("Red Dragon Fruit",                ["red dragon fruit", "dragon fruit"]),
    ("Reed Avocado",                    ["reed avocado", "reed"]),
    ("Cactus Pear",                     ["cactus pear"]),
    ("Sumo Tangerine",                  ["sumo tangerine", "sumo"]),
    ("Satsuma Mnadarin (missing cost per lb)", ["satsuma", "mandarin"]),
    ("Blood Organge",                   ["blood orange"]),
    ("Yellow Peaches",                  ["yellow peach", "peach"]),
    ("Nectarine",                       ["nectarine"]),
    ("Purple Sugar Cane",               ["purple sugar cane", "sugarcane", "sugar cane"]),
    ("Axon",                            ["axon"]),
    # "NEW OFFICIAL FB ALL" is the account-level average — no keyword match.
    # "Copy of NEW OFFICIAL FB ALL" likewise.
]


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def _parse_value(raw) -> Optional[float]:
    """
    Convert a Google Sheets cell value to float.
    Handles: percentage strings ("36%"), currency prefixes ("$126"),
    commas ("1,234"), and plain numbers. Returns None on failure.
    """
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip().replace(",", "").replace("$", "").replace(" ", "")
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------

def _build_service(credentials_path: str):
    """Build an authenticated Google Sheets API v4 service object."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _list_tabs(service, sheet_id: str) -> list:
    """Return a list of all tab names present in the spreadsheet."""
    meta = service.spreadsheets().get(
        spreadsheetId=sheet_id,
        fields="sheets.properties.title",
    ).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def _fetch_tab(service, sheet_id: str, tab: str) -> dict:
    """
    Fetch all economics fields for one tab in a single batchGet call.
    Returns {field_name: float|None}.
    """
    # Build ranges in the same order as _FIELD_ORDER so indices align
    ranges = [f"'{tab}'!{_CELL_MAP[f]}" for f in _FIELD_ORDER]

    result = service.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id,
        ranges=ranges,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()

    value_ranges = result.get("valueRanges", [])
    parsed = {}
    for i, field in enumerate(_FIELD_ORDER):
        vr = value_ranges[i] if i < len(value_ranges) else {}
        values = vr.get("values", [])
        raw = values[0][0] if values and values[0] else None
        parsed[field] = _parse_value(raw)

    return parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_unit_economics(sheet_id: str, credentials_path: str) -> dict:
    """
    Fetch unit economics for every tab in the spreadsheet.

    Returns dict keyed by tab/product name:
    {
      "Cherry Mango": {
        "aov": 126.00,
        "cogs_per_unit": 51.85,
        "margin_pct": 0.36,
        "roas_floor": 1.25,
        "breakeven_roas": 1.56,
        "roas_target_20pct_above": 1.87,
        "roas_target_28dc": 1.87,
        "roas_target_7dc": 1.68,
        "cpa_target_7dc": 75.15,
        "ltv_90d_multiplier": 0.14,
        "ltv_1yr_multiplier": 1.00,
      },
      ...
    }
    Tabs that fail to load are skipped (not fatal).
    """
    service = _build_service(credentials_path)
    tabs = _list_tabs(service, sheet_id)
    result = {}
    for tab in tabs:
        try:
            result[tab] = _fetch_tab(service, sheet_id, tab)
            print(f"  [Sheets] {tab}: OK")
        except Exception as exc:
            print(f"  [WARN] Could not load tab '{tab}': {exc}")
    return result


def match_product(campaign_name: str, economics: dict) -> Optional[str]:
    """
    Match a campaign name to a product tab key.
    Returns the tab name string, or None if no keyword matches.
    Matching is case-insensitive and uses the longest keyword first.
    """
    if not campaign_name or not economics:
        return None
    name_lower = campaign_name.lower()
    for product, keywords in _PRODUCT_KEYWORDS:
        if product not in economics:
            continue
        for kw in keywords:
            if kw in name_lower:
                return product
    return None


def classify_profitability(roas: Optional[float], econ: Optional[dict]) -> str:
    """
    Classify a campaign's profitability against its product's unit economics.

    Returns one of:
      ON_TARGET    — ROAS >= 7DC target
      PROFITABLE   — ROAS >= breakeven but below target
      UNPROFITABLE — ROAS < breakeven but above floor (20% below breakeven)
      CRITICAL     — ROAS < floor (more than 20% below breakeven)
      UNKNOWN      — no ROAS or no economics data
    """
    if roas is None or not econ:
        return "UNKNOWN"
    breakeven = econ.get("breakeven_roas")
    if breakeven is None:
        return "UNKNOWN"
    floor = econ.get("roas_floor")
    target = econ.get("roas_target_7dc") or econ.get("roas_target_28dc")
    if floor is not None and roas < floor:
        return "CRITICAL"
    if roas < breakeven:
        return "UNPROFITABLE"
    if target is not None and roas >= target:
        return "ON_TARGET"
    return "PROFITABLE"


def detect_economics_changes(
    current: dict,
    snapshot_dir: str,
    campaign_names: Optional[list] = None,
) -> list:
    """
    Compare current economics against the most recent snapshot.
    Saves current as a new snapshot (outputs/economics_snapshots/economics_YYYY-MM-DD.json).

    Returns list of change dicts:
    [
      {
        "product": "Cherry Mango",
        "field": "breakeven_roas",
        "old_value": 1.56,
        "new_value": 1.72,
        "affected_campaigns": ["..."],
        "profitability_flip": True,
        "impact_summary": "Breakeven ROAS changed 1.56x → 1.72x",
      }
    ]
    Returns [] if no previous snapshot exists (first run).
    """
    snap_dir = Path(snapshot_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Load the most recent snapshot
    snapshots = sorted(snap_dir.glob("economics_????-??-??.json"), reverse=True)
    previous: dict = {}
    if snapshots:
        try:
            previous = json.loads(snapshots[0].read_text())
        except Exception:
            previous = {}

    # Save current as today's snapshot (overwrite same-day file if re-run)
    today = date.today().isoformat()
    (snap_dir / f"economics_{today}.json").write_text(
        json.dumps(current, indent=2, default=str)
    )

    if not previous:
        return []

    # Fields where a change might flip profitability
    _PROFITABILITY_FIELDS = {"breakeven_roas", "roas_floor", "roas_target_7dc"}

    changes = []
    for product, curr_econ in current.items():
        prev_econ = previous.get(product)
        if not prev_econ:
            continue
        for field in _FIELD_ORDER:
            old_val = prev_econ.get(field)
            new_val = curr_econ.get(field)
            if old_val == new_val:
                continue
            if old_val is None and new_val is None:
                continue

            flip = field in _PROFITABILITY_FIELDS and old_val is not None and new_val is not None

            # Build impact summary
            if field == "breakeven_roas" and old_val and new_val:
                impact = f"Breakeven ROAS changed {old_val:.2f}x → {new_val:.2f}x"
            elif field == "margin_pct" and old_val and new_val:
                impact = (
                    f"Gross margin changed "
                    f"{old_val*100:.1f}% → {new_val*100:.1f}%"
                )
            elif field == "cpa_target_7dc" and old_val and new_val:
                impact = f"7DC CPA target changed ${old_val:.2f} → ${new_val:.2f}"
            else:
                old_str = f"{old_val:.2f}" if isinstance(old_val, float) else str(old_val)
                new_str = f"{new_val:.2f}" if isinstance(new_val, float) else str(new_val)
                impact = f"{field}: {old_str} → {new_str}"

            # Find affected campaigns by matching product keyword
            affected = []
            if campaign_names and flip:
                for cname in campaign_names:
                    if match_product(cname, current) == product:
                        affected.append(cname)

            changes.append({
                "product": product,
                "field": field,
                "old_value": old_val,
                "new_value": new_val,
                "affected_campaigns": affected,
                "profitability_flip": flip,
                "impact_summary": impact,
            })

    return changes
