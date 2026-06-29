"""
Wardrobe analytics — computed from catalog + OOTD entries.

Called by /stats command in the bot.
"""

from datetime import date, datetime, timedelta
import notion


def compute(cfg: dict, catalog: list[dict]) -> dict:
    """
    Fetch OOTD entries and compute all analytics.
    Returns a dict with keys: summary, coverage, cpw, top_worn, dead_weight, streaks.
    """
    ootd_entries = notion.fetch_ootd_entries(cfg, limit=1000)

    # Build wear counts per item
    wear_counts: dict[str, int] = {}
    last_worn: dict[str, str] = {}
    for entry in ootd_entries:
        d = entry["date"]
        for item_id in entry["item_ids"]:
            wear_counts[item_id] = wear_counts.get(item_id, 0) + 1
            if item_id not in last_worn or d > last_worn[item_id]:
                last_worn[item_id] = d

    today = date.today()
    total_items = len(catalog)
    items_with_price = [i for i in catalog if i.get("price") and i["price"] > 0]
    ever_worn = [i for i in catalog if wear_counts.get(i["id"], 0) > 0]
    never_worn = [i for i in catalog if wear_counts.get(i["id"], 0) == 0]

    # Coverage
    coverage_pct = round(len(ever_worn) / max(total_items, 1) * 100)

    # Avg CPW (items with price and at least 1 wear)
    cpw_items = [
        i["price"] / wear_counts[i["id"]]
        for i in items_with_price
        if wear_counts.get(i["id"], 0) > 0
    ]
    avg_cpw = round(sum(cpw_items) / len(cpw_items), 2) if cpw_items else None

    # Top worn (by count)
    top_worn = sorted(
        [(wear_counts.get(i["id"], 0), i) for i in catalog],
        key=lambda x: -x[0]
    )[:5]

    # Dead weight: owned and never worn (exclude recently added items < 30 days)
    # We don't have acquisition date in the minimal schema, so just show never-worn
    dead_weight_items = never_worn[:10]

    # Not worn in last 12 months
    cutoff_12m = (today - timedelta(days=365)).isoformat()
    stale = [
        i for i in catalog
        if i["id"] in last_worn and last_worn[i["id"]] < cutoff_12m
    ]

    # Outfits logged
    total_outfits = len(ootd_entries)

    # Sustainability score (0-100)
    score = _sustainability_score(coverage_pct, avg_cpw, total_items, len(never_worn))

    return {
        "total_items": total_items,
        "total_outfits": total_outfits,
        "ever_worn": len(ever_worn),
        "never_worn": len(never_worn),
        "coverage_pct": coverage_pct,
        "avg_cpw": avg_cpw,
        "top_worn": top_worn,
        "dead_weight": dead_weight_items,
        "stale_12m": len(stale),
        "score": score,
    }


def _sustainability_score(coverage_pct: int, avg_cpw: float | None, total_items: int, never_worn: int) -> int:
    score = 0

    # Coverage (40 pts): % of wardrobe worn at least once
    score += min(40, round(coverage_pct * 0.4))

    # CPW efficiency (30 pts): lower is better. Target: good CPW = at most 3% of avg price assumption
    # We use a simple heuristic: CPW < 5 = 30 pts, CPW < 15 = 20 pts, CPW < 30 = 10 pts
    if avg_cpw is not None:
        if avg_cpw < 5:
            score += 30
        elif avg_cpw < 15:
            score += 20
        elif avg_cpw < 30:
            score += 10

    # Dead weight (30 pts): items never worn as % of total
    if total_items > 0:
        dead_pct = never_worn / total_items * 100
        if dead_pct < 5:
            score += 30
        elif dead_pct < 15:
            score += 20
        elif dead_pct < 30:
            score += 10

    return min(score, 100)


def format_stats_message(data: dict) -> str:
    lines = ["<b>Your wardrobe stats</b>\n"]

    lines.append(f"📦 <b>{data['total_items']} items</b> in your archive")
    lines.append(f"📸 <b>{data['total_outfits']} outfits</b> logged\n")

    cov = data['coverage_pct']
    lines.append(f"<b>Coverage</b>")
    bar = _progress_bar(cov)
    lines.append(f"{bar} {cov}% worn at least once")
    lines.append(f"  Worn: {data['ever_worn']} · Never worn: {data['never_worn']}\n")

    if data['avg_cpw'] is not None:
        lines.append(f"<b>Average cost-per-wear</b>")
        lines.append(f"  ${data['avg_cpw']:.2f} per wear\n")

    if data['top_worn']:
        lines.append("<b>Most worn</b>")
        for count, item in data['top_worn']:
            if count > 0:
                lines.append(f"  {count}× {item['name']}")
        lines.append("")

    if data['never_worn']:
        lines.append(f"<b>Never worn ({data['never_worn']} items)</b>")
        for item in data['dead_weight'][:5]:
            lines.append(f"  · {item['name']}")
        if data['never_worn'] > 5:
            lines.append(f"  · ... and {data['never_worn'] - 5} more")
        lines.append("")

    if data['stale_12m']:
        lines.append(f"⚠️ <b>{data['stale_12m']} items</b> not worn in the last 12 months")
        lines.append("")

    score = data['score']
    lines.append(f"<b>Wardrobe health score: {score}/100</b>")
    if score >= 80:
        lines.append("Your closet is working hard. Keep it up.")
    elif score >= 60:
        lines.append("Good foundation. A few unworn pieces are dragging the score.")
    elif score >= 40:
        lines.append("Room to improve. Focus on wearing what you own.")
    else:
        lines.append("Start logging more outfits to see your score climb.")

    return "\n".join(lines)


def _progress_bar(pct: int, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)
