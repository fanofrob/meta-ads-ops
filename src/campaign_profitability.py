"""
campaign_profitability.py

Maps Meta campaign names to product economics and scores each campaign
against its unit economics targets.
"""

from typing import Optional

PRODUCT_MAP = [
    # Order matters — more specific rules first
    {
        "product_key": "Cherry Plum (BC)",
        "tab_name": "Cherry Plum (BC)",
        "match_all": ["cherry plum", "bc"],
        "match_any": [],
    },
    {
        "product_key": "Cherry Plum (tROAS)",
        "tab_name": "Cherry Plum (BC)",
        "match_all": ["cherry plum", "troas"],
        "match_any": [],
    },
    {
        "product_key": "Cherry Mango",
        "tab_name": "Cherry Mango",
        "match_all": ["cherry mango"],
        "match_any": [],
    },
    {
        "product_key": "Cherimoya",
        "tab_name": "Cherimoya",
        "match_all": ["cherimoya"],
        "match_any": [],
    },
    {
        "product_key": "Pink Pineapple",
        "tab_name": "Pink Pineapple (BC)",
        "match_all": ["pink pineapple"],
        "match_any": [],
    },
    {
        "product_key": "Bing Cherry",
        "tab_name": "Bing Cherry (BC)",
        "match_all": [],
        "match_any": ["bing cherry", "red cherry"],
    },
    {
        "product_key": "Rainier Cherry",
        "tab_name": "Rainier Cherry (BC)",
        "match_all": ["rainier"],
        "match_any": [],
    },
    {
        "product_key": "Purple Passion Fruit",
        "tab_name": "Purple Passion Fruit (BC)",
        "match_all": ["passion fruit"],
        "match_any": [],
    },
    {
        "product_key": "Fuyu Persimmon",
        "tab_name": "Fuyu Persimmon (BC)",
        "match_all": ["fuyu persimmon"],
        "match_any": [],
    },
    {
        "product_key": "Fuyu Persimmon",
        "tab_name": "Fuyu Persimmon (BC)",
        "match_all": ["persimmons"],
        "match_any": [],
    },
    {
        "product_key": "Chocolate Persimmon",
        "tab_name": "Chocolate Persimmon",
        "match_all": ["chocolate persimmon"],
        "match_any": [],
    },
    {
        "product_key": "Mangosteen",
        "tab_name": "Mangosteen (BC)",
        "match_all": ["mangosteen"],
        "match_any": [],
    },
    {
        "product_key": "Honey Mango",
        "tab_name": "Honey Mango",
        "match_all": ["honey mango"],
        "match_any": [],
    },
    {
        "product_key": "Lychee",
        "tab_name": "Lychee",
        "match_all": ["lychee"],
        "match_any": [],
    },
    {
        "product_key": "Loquat",
        "tab_name": "Loquat",
        "match_all": ["loquat"],
        "match_any": [],
    },
    {
        "product_key": "Pineapple Guava",
        "tab_name": "Pineapple Guava (needs price per lb)",
        "match_all": ["pineapple guava"],
        "match_any": [],
    },
    {
        "product_key": "Cotton Candy Grape",
        "tab_name": "Cotton Candy Grape (needs price per lb)",
        "match_all": ["cotton candy grape"],
        "match_any": [],
    },
    {
        "product_key": "Pomegranate",
        "tab_name": "Pomegranate",
        "match_all": ["pomegranate"],
        "match_any": [],
    },
    {
        "product_key": "Red Dragon Fruit",
        "tab_name": "Red Dragon Fruit",
        "match_all": ["dragon fruit"],
        "match_any": [],
    },
    {
        "product_key": "Reed Avocado",
        "tab_name": "Reed Avocado",
        "match_all": ["reed avocado"],
        "match_any": [],
    },
    {
        "product_key": "Cactus Pear",
        "tab_name": "Cactus Pear",
        "match_all": ["cactus pear"],
        "match_any": [],
    },
    {
        "product_key": "Sumo Tangerine",
        "tab_name": "Sumo Tangerine",
        "match_all": ["sumo tangerine"],
        "match_any": [],
    },
    {
        "product_key": "Satsuma Mandarin",
        "tab_name": "Satsuma Mnadarin (missing cost per lb)",
        "match_all": ["satsuma"],
        "match_any": [],
    },
    {
        "product_key": "Blood Orange",
        "tab_name": "Blood Organge",
        "match_all": ["blood orange"],
        "match_any": [],
    },
    {
        "product_key": "Yellow Peaches",
        "tab_name": "Yellow Peaches",
        "match_all": [],
        "match_any": ["yellow peach", "peach"],
    },
    {
        "product_key": "Nectarine",
        "tab_name": "Nectarine",
        "match_all": ["nectarine"],
        "match_any": [],
    },
    {
        "product_key": "Purple Sugar Cane",
        "tab_name": "Purple Sugar Cane",
        "match_all": [],
        "match_any": ["sugar cane", "sugarcane"],
    },
    {
        "product_key": "Hawaiian Papaya",
        "tab_name": "Hawaiian Papaya",
        "match_all": ["hawaiian papaya"],
        "match_any": [],
    },
    {
        "product_key": "Papaya",
        "tab_name": "Papaya",
        "match_all": ["papaya"],
        "match_any": [],
    },
    {
        "product_key": "Variety Box",
        "tab_name": "Variety Box",
        "match_all": ["variety box"],
        "match_any": [],
    },
    {
        "product_key": "Axon",
        "tab_name": "Axon",
        "match_all": ["axon"],
        "match_any": [],
    },
    {
        "product_key": "Blended",
        "tab_name": "NEW OFFICIAL FB ALL (Avrg accross SKUs)",
        "match_all": [],
        "match_any": ["mix", "retention", "blended", "dpa", "longan"],
        "note": "Multi-product — economics are approximate",
    },
]


def match_product_rule(campaign_name: str) -> Optional[dict]:
    """
    Match a campaign name to the first matching PRODUCT_MAP rule.
    Returns the rule dict or None if no match.
    Case-insensitive substring matching.
    match_all: ALL strings must appear in the campaign name.
    match_any: AT LEAST ONE string must appear (if non-empty).
    If both match_all and match_any are empty — never matches as a solo rule.
    """
    name_lower = campaign_name.lower()
    for rule in PRODUCT_MAP:
        match_all = rule.get("match_all", [])
        match_any = rule.get("match_any", [])

        if match_all and not all(kw in name_lower for kw in match_all):
            continue
        if match_any and not any(kw in name_lower for kw in match_any):
            continue
        if not match_all and not match_any:
            continue

        return rule
    return None


def score_campaign(campaign: dict, economics_by_product: dict) -> dict:
    """
    Score a campaign against its unit economics.

    campaign dict must contain: name, spend, roas, cpa, purchases, revenue
    economics_by_product is keyed by TAB NAME (not product_key).

    Returns a scoring dict with profitability status and targets.
    """
    name = campaign.get("name", "")
    spend = campaign.get("spend") or 0
    roas = campaign.get("roas")
    cpa = campaign.get("cpa")
    purchases = campaign.get("purchases") or 0
    revenue = campaign.get("revenue") or 0

    rule = match_product_rule(name)

    if rule is None:
        return {
            "product_key": "UNKNOWN",
            "tab_name": None,
            "breakeven_roas": None,
            "roas_floor": None,
            "roas_target_7dc": None,
            "cpa_target_7dc": None,
            "profitability_status": "UNKNOWN",
            "vs_breakeven_pct": None,
            "vs_7dc_target_pct": None,
            "estimated_daily_profit": None,
            "cpa_vs_target_pct": None,
            "is_blended": False,
            "blended_note": None,
            "aov_warning": None,
            "automation_safe": False,
            "automation_safe_reason": "No product match — economics unavailable",
        }

    tab_name = rule["tab_name"]
    econ = economics_by_product.get(tab_name)
    is_blended = rule.get("product_key") == "Blended"
    blended_note = rule.get("note")

    if econ is None:
        return {
            "product_key": rule["product_key"],
            "tab_name": tab_name,
            "breakeven_roas": None,
            "roas_floor": None,
            "roas_target_7dc": None,
            "cpa_target_7dc": None,
            "profitability_status": "UNKNOWN",
            "vs_breakeven_pct": None,
            "vs_7dc_target_pct": None,
            "estimated_daily_profit": None,
            "cpa_vs_target_pct": None,
            "is_blended": is_blended,
            "blended_note": blended_note,
            "aov_warning": None,
            "automation_safe": False,
            "automation_safe_reason": f"Economics not loaded for tab '{tab_name}'",
        }

    breakeven = econ.get("breakeven_roas")
    roas_floor = econ.get("roas_floor")
    target_7dc = econ.get("roas_target_7dc")
    cpa_target = econ.get("cpa_target_7dc")
    aov_sheet = econ.get("aov")
    cogs_per_unit = econ.get("cogs_per_unit")

    # Profitability classification
    if roas is None or breakeven is None:
        if spend >= 25 and purchases == 0:
            profitability_status = "UNPROFITABLE"
        else:
            profitability_status = "UNKNOWN"
    elif roas > breakeven * 1.05:
        profitability_status = "PROFITABLE"
    elif roas < breakeven * 0.95:
        profitability_status = "UNPROFITABLE"
    else:
        profitability_status = "BREAKEVEN"

    # Derived metrics
    vs_breakeven_pct = None
    if roas is not None and breakeven:
        vs_breakeven_pct = round((roas / breakeven - 1) * 100, 1)

    vs_7dc_target_pct = None
    if roas is not None and target_7dc:
        vs_7dc_target_pct = round((roas / target_7dc - 1) * 100, 1)

    cpa_vs_target_pct = None
    if cpa is not None and cpa_target:
        cpa_vs_target_pct = round((cpa / cpa_target - 1) * 100, 1)

    # Estimated daily profit: revenue - spend - (cogs * purchases)
    estimated_daily_profit = None
    if purchases > 0 and cogs_per_unit is not None:
        estimated_daily_profit = round(revenue - spend - (cogs_per_unit * purchases), 2)
    elif profitability_status == "UNPROFITABLE" and spend > 0 and purchases == 0:
        estimated_daily_profit = -spend

    # AOV sanity check
    aov_warning = None
    if aov_sheet and purchases > 0 and revenue > 0:
        actual_aov = revenue / purchases
        if abs(actual_aov - aov_sheet) / aov_sheet > 0.20:
            aov_warning = (
                f"Actual AOV ${actual_aov:.0f} vs expected ${aov_sheet:.0f} — "
                "margin calculations may be off"
            )

    # Automation safety
    automation_safe = True
    automation_safe_reason = None
    if profitability_status == "UNKNOWN":
        automation_safe = False
        automation_safe_reason = "Profitability unknown — no economics data or no ROAS"
    elif is_blended:
        automation_safe = False
        automation_safe_reason = "Blended/multi-product campaign — economics are approximate"
    elif aov_warning:
        automation_safe = False
        automation_safe_reason = f"AOV mismatch: {aov_warning}"

    return {
        "product_key": rule["product_key"],
        "tab_name": tab_name,
        "breakeven_roas": breakeven,
        "roas_floor": roas_floor,
        "roas_target_7dc": target_7dc,
        "cpa_target_7dc": cpa_target,
        "profitability_status": profitability_status,
        "vs_breakeven_pct": vs_breakeven_pct,
        "vs_7dc_target_pct": vs_7dc_target_pct,
        "estimated_daily_profit": estimated_daily_profit,
        "cpa_vs_target_pct": cpa_vs_target_pct,
        "is_blended": is_blended,
        "blended_note": blended_note,
        "aov_warning": aov_warning,
        "automation_safe": automation_safe,
        "automation_safe_reason": automation_safe_reason,
    }
