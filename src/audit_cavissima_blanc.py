# src/audit_cavissima_blanc.py
"""
One-off audit: find and correct cavissima Blanc price_records where
the scraper may have stored a case total instead of a unit price.

For each historical cavissima Blanc record we:
  1. Compare against the median from all other retailers for the same
     estate + vintage (Blanc only).
  2. Classify the record:
       OK           — within ±30% of other-retailer median
       CASE_X6      — price ≈ 6× median → auto-corrected to price / 6
       CASE_X12     — price ≈ 12× median → auto-corrected to price / 12
       FLAG_HIGH    — >1.3× median but not a clean case multiple
       FLAG_LOW     — <0.7× median
       UNVERIFIABLE — no other-retailer data to compare against
  3. Apply DB corrections for high-confidence case errors (CASE_X6 / X12).
  4. Write full audit to the GSheet 'cavissima_blanc_audit' tab.

Run via: python -m src.audit_cavissima_blanc
Or trigger the audit_cavissima_blanc GitHub Actions workflow.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text

from src.db import engine, SessionLocal
from src.export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

AUDIT_TAB = "cavissima_blanc_audit"

# Ratio bands for case-price detection
CASE6_LO,  CASE6_HI  = 5.0,  7.0
CASE12_LO, CASE12_HI = 10.0, 14.0
OK_LO,     OK_HI     = 0.70, 1.30


def _classify(ratio: float | None) -> str:
    if ratio is None:
        return "UNVERIFIABLE"
    if CASE12_LO <= ratio <= CASE12_HI:
        return "CASE_X12"
    if CASE6_LO <= ratio <= CASE6_HI:
        return "CASE_X6"
    if ratio > OK_HI:
        return "FLAG_HIGH"
    if ratio < OK_LO:
        return "FLAG_LOW"
    return "OK"


def _load_cavissima_blanc() -> pd.DataFrame:
    return pd.read_sql(
        """
        SELECT
            pr.id,
            mp.estate_name,
            pr.vintage,
            pr.price_amount,
            pr.price_corrected,
            pr.original_price,
            pr.correction_reason,
            pr.raw_price_text,
            pr.url,
            DATE(pr.fetched_at AT TIME ZONE 'UTC') AS scrape_date
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        WHERE mp.retailer   = 'cavissima'
          AND mp.wine_color = 'Blanc'
          AND pr.price_amount IS NOT NULL
          AND pr.price_amount > 0
        ORDER BY mp.estate_name, pr.vintage, pr.fetched_at
        """,
        engine,
    )


def _other_retailer_medians() -> pd.DataFrame:
    """Median price per (estate_name, vintage) from all non-cavissima Blanc records."""
    return pd.read_sql(
        """
        SELECT
            mp.estate_name,
            pr.vintage,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pr.price_amount) AS median_price,
            COUNT(DISTINCT mp.retailer) AS retailer_count
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        WHERE mp.retailer   != 'cavissima'
          AND mp.wine_color  = 'Blanc'
          AND pr.price_amount IS NOT NULL
          AND pr.price_amount > 0
        GROUP BY mp.estate_name, pr.vintage
        """,
        engine,
    )


def run_audit(dry_run: bool = False) -> list[dict]:
    cavissima = _load_cavissima_blanc()
    medians   = _other_retailer_medians()

    # Build lookup: (estate_name, vintage) → (median_price, retailer_count)
    median_lookup: dict[tuple, tuple] = {}
    for _, r in medians.iterrows():
        vintage = None if pd.isna(r.vintage) else int(r.vintage)
        median_lookup[(r.estate_name, vintage)] = (float(r.median_price), int(r.retailer_count))

    results = []
    corrections: list[dict] = []

    for _, row in cavissima.iterrows():
        vintage = None if pd.isna(row.vintage) else int(row.vintage)
        price   = float(row.price_amount)
        key     = (row.estate_name, vintage)
        median_info = median_lookup.get(key)

        if median_info:
            median_price, retailer_count = median_info
            ratio = round(price / median_price, 2)
        else:
            median_price = retailer_count = ratio = None

        status = _classify(ratio)

        corrected_price = None
        if status == "CASE_X6":
            corrected_price = round(price / 6, 2)
        elif status == "CASE_X12":
            corrected_price = round(price / 12, 2)

        results.append({
            "id":              int(row.id),
            "estate_name":     row.estate_name,
            "vintage":         vintage if vintage is not None else "",
            "scrape_date":     str(row.scrape_date),
            "cavissima_price": round(price, 2),
            "other_median":    round(median_price, 2) if median_price else "",
            "retailers_used":  retailer_count if retailer_count else "",
            "ratio":           ratio if ratio else "",
            "status":          status,
            "corrected_to":    corrected_price if corrected_price else "",
            "already_fixed":   bool(row.price_corrected),
            "raw_price_text":  row.raw_price_text or "",
            "url":             row.url or "",
        })

        if corrected_price and not bool(row.price_corrected):
            corrections.append({
                "id":               int(row.id),
                "original_price":   price,
                "corrected_price":  corrected_price,
                "reason":           f"audit: case price ÷{'6' if status == 'CASE_X6' else '12'} "
                                    f"({ratio:.1f}× other-retailer median)",
            })

    logger.info(
        f"Audit complete: {len(results)} records, "
        f"{sum(1 for r in results if r['status'] == 'OK')} OK, "
        f"{sum(1 for r in results if r['status'].startswith('CASE'))} case-price errors, "
        f"{sum(1 for r in results if r['status'].startswith('FLAG'))} flagged, "
        f"{sum(1 for r in results if r['status'] == 'UNVERIFIABLE')} unverifiable"
    )

    if corrections:
        logger.info(f"{'DRY RUN — ' if dry_run else ''}Applying {len(corrections)} DB corrections")
        if not dry_run:
            _apply_corrections(corrections)
    else:
        logger.info("No corrections needed.")

    return results


def _apply_corrections(corrections: list[dict]) -> None:
    with engine.begin() as conn:
        for c in corrections:
            conn.execute(
                text("""
                    UPDATE price_records
                    SET price_amount      = :corrected,
                        original_price    = :original,
                        price_corrected   = TRUE,
                        correction_reason = :reason
                    WHERE id = :id
                      AND price_corrected = FALSE
                """),
                {
                    "corrected": c["corrected_price"],
                    "original":  c["original_price"],
                    "reason":    c["reason"],
                    "id":        c["id"],
                },
            )
            logger.info(
                f"  Corrected record {c['id']}: "
                f"{c['original_price']:.2f} → {c['corrected_price']:.2f} EUR"
            )


def export_audit_to_gsheet(results: list[dict]) -> None:
    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)

    try:
        ws = sheet.worksheet(AUDIT_TAB)
        ws.clear()
        logger.info(f"Cleared existing '{AUDIT_TAB}' tab")
    except Exception:
        ws = sheet.add_worksheet(title=AUDIT_TAB, rows=2000, cols=14)
        logger.info(f"Created new '{AUDIT_TAB}' tab")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    n_ok           = sum(1 for r in results if r["status"] == "OK")
    n_case         = sum(1 for r in results if r["status"].startswith("CASE"))
    n_flag         = sum(1 for r in results if r["status"].startswith("FLAG"))
    n_unverifiable = sum(1 for r in results if r["status"] == "UNVERIFIABLE")

    headers = [
        "id", "estate_name", "vintage", "scrape_date",
        "cavissima_price_€", "other_retailer_median_€", "retailers_used",
        "ratio", "status", "corrected_to_€",
        "already_fixed_by_validator", "raw_price_text", "url",
    ]

    rows = [
        [f"Cavissima Blanc audit — {generated}",
         f"OK: {n_ok}", f"Case errors: {n_case}",
         f"Flagged: {n_flag}", f"Unverifiable: {n_unverifiable}",
         "", "", "", "", "", "", "", ""],
        [""] * 13,
        headers,
    ]

    for r in results:
        rows.append([
            r["id"], r["estate_name"], r["vintage"], r["scrape_date"],
            r["cavissima_price"], r["other_median"], r["retailers_used"],
            r["ratio"], r["status"], r["corrected_to"],
            r["already_fixed"], r["raw_price_text"], r["url"],
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    try:
        ws.format("A1:M1", {"textFormat": {"bold": True}})
        ws.format("A3:M3", {"textFormat": {"bold": True}})
    except Exception:
        pass

    logger.info(
        f"Audit results written to '{GOOGLE_SHEET_NAME}' > '{AUDIT_TAB}' "
        f"({len(results)} data rows)"
    )


def main() -> None:
    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
    results = run_audit(dry_run=dry_run)
    export_audit_to_gsheet(results)


if __name__ == "__main__":
    main()
