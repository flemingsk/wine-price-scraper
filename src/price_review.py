# src/price_review.py
"""
Post-scrape manual review list — runs after each daily scrape.

Flags today's price_records that need human verification:
  - price < LOW_PRICE_THRESHOLD (25 €) for non-exempt estates
  - price > HIGH_PRICE_THRESHOLD (70 €)

Items already approved in the GSheet 'price_review_approved' tab are
suppressed unless the current price deviates very far from the SKU median
(> FAR_DEVIATION_RATIO × median), which signals a real problem even for
a previously-verified SKU.

Output: refreshes the GSheet 'price_review' tab with today's flagged items.

Approved list format (price_review_approved tab, user-maintained):
  estate_name | retailer | vintage | note
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from src.db import engine
from src.export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────────────
LOW_PRICE_THRESHOLD  = 25.0
HIGH_PRICE_THRESHOLD = 70.0
FAR_DEVIATION_RATIO  = 3.0   # re-flag an approved SKU if price/median exceeds this

EXEMPT_ESTATES = {
    "Chateau Lespault-Martillac",
    "Chateau La Louviere",
}

REVIEW_TAB   = "price_review"
APPROVED_TAB = "price_review_approved"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_approved(client) -> set[tuple[str, str, int]]:
    """
    Read the 'price_review_approved' tab.
    Returns a set of (estate_name, retailer, vintage) tuples the user has
    verified as OK and wants to suppress from future reviews.
    Returns empty set if the tab doesn't exist yet.
    """
    import gspread
    try:
        sheet = client.open(GOOGLE_SHEET_NAME)
        ws = sheet.worksheet(APPROVED_TAB)
    except gspread.exceptions.WorksheetNotFound:
        return set()

    rows = ws.get_all_values()
    if len(rows) < 2:
        return set()

    headers = [h.strip().lower() for h in rows[0]]
    try:
        i_estate   = headers.index("estate_name")
        i_retailer = headers.index("retailer")
        i_vintage  = headers.index("vintage")
    except ValueError:
        logger.warning(
            f"'{APPROVED_TAB}' tab missing expected columns "
            "(estate_name, retailer, vintage). Skipping approved list."
        )
        return set()

    approved = set()
    for row in rows[1:]:
        try:
            estate   = row[i_estate].strip()
            retailer = row[i_retailer].strip()
            vintage  = int(row[i_vintage])
            if estate and retailer and vintage:
                approved.add((estate, retailer, vintage))
        except (ValueError, IndexError):
            continue
    return approved


def _sku_medians(today) -> dict[tuple, float]:
    """
    Return a dict of (master_product_id, vintage) → historical median price,
    excluding today's records so we're comparing against established history.
    """
    df = pd.read_sql(
        """
        SELECT master_product_id, vintage,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_amount) AS median_price
        FROM price_records
        WHERE price_amount > 0
          AND price_amount IS NOT NULL
          AND DATE(fetched_at) < %(today)s
        GROUP BY master_product_id, vintage
        """,
        engine, params={"today": today},
    )
    return {(int(r.master_product_id), int(r.vintage)): float(r.median_price) for _, r in df.iterrows()}


def export_price_review() -> None:
    """
    Query today's scrape, flag suspicious prices, write to GSheet 'price_review'.
    """
    today = datetime.now(timezone.utc).date()

    # ── Query today's price records ─────────────────────────────────────────
    df = pd.read_sql(
        """
        SELECT
            pr.id,
            mp.estate_name,
            pr.site          AS retailer,
            pr.vintage,
            pr.wine_color,
            pr.price_amount,
            pr.url,
            pr.master_product_id
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        WHERE DATE(pr.fetched_at) = %(today)s
          AND pr.price_amount IS NOT NULL
          AND pr.price_amount > 0
        ORDER BY mp.estate_name, pr.site, pr.vintage
        """,
        engine, params={"today": today},
    )

    if df.empty:
        logger.info("price_review: no records for today — skipping.")
        return

    medians = _sku_medians(today)

    # ── Flag suspicious rows ────────────────────────────────────────────────
    flagged = []
    for _, row in df.iterrows():
        price  = float(row["price_amount"])
        estate = row["estate_name"]
        reason = None

        if price < LOW_PRICE_THRESHOLD and estate not in EXEMPT_ESTATES:
            reason = f"below {LOW_PRICE_THRESHOLD:.0f} € threshold"
        elif price > HIGH_PRICE_THRESHOLD:
            reason = f"above {HIGH_PRICE_THRESHOLD:.0f} € threshold"

        if reason is None:
            continue

        key    = (int(row["master_product_id"]), int(row["vintage"]))
        median = medians.get(key)
        ratio  = round(price / median, 2) if median else None

        flagged.append({
            "estate_name":     estate,
            "retailer":        row["retailer"],
            "vintage":         int(row["vintage"]),
            "wine_color":      row["wine_color"],
            "price_€":         round(price, 2),
            "flag_reason":     reason,
            "sku_median_€":    round(median, 2) if median else "",
            "ratio_to_median": ratio if ratio else "",
            "url":             row["url"],
        })

    if not flagged:
        logger.info(f"price_review: no suspicious prices for {today}.")
        _clear_review_tab()
        return

    # ── Load approved list and suppress known-OK items ──────────────────────
    client   = _get_gsheet_client()
    approved = _load_approved(client)

    final = []
    suppressed = 0
    for item in flagged:
        key = (item["estate_name"], item["retailer"], item["vintage"])
        if key in approved:
            ratio = item["ratio_to_median"]
            if ratio and isinstance(ratio, float) and ratio > FAR_DEVIATION_RATIO:
                item["flag_reason"] += f" [APPROVED but {ratio:.1f}x median — re-flagged]"
            else:
                suppressed += 1
                continue
        final.append(item)

    logger.info(
        f"price_review: {len(flagged)} flagged, {suppressed} suppressed (approved), "
        f"{len(final)} to write for {today}."
    )

    # ── Write to GSheet ─────────────────────────────────────────────────────
    sheet = client.open(GOOGLE_SHEET_NAME)
    try:
        import gspread
        ws = sheet.worksheet(REVIEW_TAB)
    except Exception:
        ws = sheet.add_worksheet(title=REVIEW_TAB, rows=500, cols=12)

    ws.clear()

    headers = [
        "date", "estate_name", "retailer", "vintage", "wine_color",
        "price_€", "flag_reason", "sku_median_€", "ratio_to_median", "url",
    ]
    rows = [headers]
    for item in final:
        rows.append([
            str(today),
            item["estate_name"],
            item["retailer"],
            item["vintage"],
            item["wine_color"],
            item["price_€"],
            item["flag_reason"],
            item["sku_median_€"],
            item["ratio_to_median"],
            item["url"],
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    # Bold the header row
    try:
        ws.format("A1:J1", {"textFormat": {"bold": True}})
    except Exception:
        pass

    print(
        f"price_review: wrote {len(final)} flagged prices to "
        f"'{GOOGLE_SHEET_NAME}' > '{REVIEW_TAB}' for {today}."
    )


def _clear_review_tab() -> None:
    """Clear the price_review tab when there's nothing to flag today."""
    try:
        client = _get_gsheet_client()
        sheet  = client.open(GOOGLE_SHEET_NAME)
        ws     = sheet.worksheet(REVIEW_TAB)
        ws.clear()
        ws.update([["No suspicious prices flagged for today."]])
    except Exception:
        pass
