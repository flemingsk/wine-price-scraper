# src/process_flags.py
"""
User-feedback processing: reads 'pending' rows from the 'flags' GSheet tab
and applies corrections to the DB before each scrape run.

AUTO-FIXABLE ISSUE TYPES
  incorrect-url   URL fetches the wrong product.
                  corrected_url required. Updates product_url in DB; keeps price history.

  wrong-format    URL is for the wrong bottle size (magnum, half-bottle, case price, wrong variant).
                  WITHOUT corrected_url: product deactivated, price records deleted.
                  WITH corrected_url: URL replaced, wrong-format records deleted, product stays active.

  404             Product removed or URL permanently broken.
                  WITHOUT corrected_url: product deactivated, price records deleted.
                  WITH corrected_url: same as wrong-format with replacement.

  validated-ok    Price flag is expected / a known anomaly — not a data error.
                  No DB change. Suppresses (estate, retailer, vintage) from Section 2 permanently.
                  Delete the flags row to re-enable.

NEEDS-MANUAL-ACTION (workflow flags but cannot auto-fix)
  wrong-estate, wrong-vintage, wrong-price, duplicate, other
  → Update master_products.csv manually, commit with [skip ci].

NOTE: DB changes are immediate. master_products.csv must be updated manually to stay in sync.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from .db import engine
from .export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME

logger = logging.getLogger(__name__)

FLAGS_TAB = "flags"

FLAGS_HEADER = [
    "date_flagged", "source", "estate_name", "retailer", "vintage", "price",
    "current_url", "issue_type", "corrected_url", "notes", "status", "processed_at",
]

# Dropdown values for issue_type — kept here as the single source of truth.
ISSUE_TYPES = [
    "incorrect-url",   # wrong product URL; corrected_url required
    "wrong-format",    # wrong bottle size; corrected_url optional (enables URL swap)
    "404",             # product gone; corrected_url optional
    "validated-ok",    # price flag is correct — suppress from Section 2
    "wrong-estate",    # attribution error — manual CSV fix
    "wrong-vintage",   # vintage error — manual CSV fix
    "wrong-price",     # scraper parsing error — manual investigation
    "other",           # other — manual investigation
]

# Dropdown values for status — user-settable values listed first.
STATUS_VALUES = [
    "auto-detected",       # set by system; awaiting review
    "pending",             # user confirmed; workflow processes next run
    "processed",           # workflow applied fix
    "validated",           # false positive; suppressed from Section 2
    "needs-manual-action", # workflow cannot auto-fix
    "error",               # workflow error; check logs
]

# README written to column M of the flags tab on every run — always up to date.
FLAGS_README = [
    "=== FLAGS TAB — USER GUIDE ===",
    "This sidebar is auto-updated on every scrape run.",
    "",
    "PURPOSE",
    "Feedback loop between Section 2 (Price Flags) of daily_report",
    "and automated DB corrections applied before each scrape run.",
    "",
    "HOW TO USE",
    "1. Open daily_report tab → Section 2 shows anomalies detected today.",
    "2. Click the URL to verify the issue on the retailer site.",
    "3. Find the pre-filled row in this tab (auto-detected from Section 2).",
    "   Or add a row manually for issues you spotted directly.",
    "4. Fill in issue_type (col G) from the ISSUE TYPES list below.",
    "5. Fill corrected_url (col H) if issue_type is incorrect-url",
    "   or wrong-format / 404 with a known replacement URL.",
    "6. Change status (col J) from 'auto-detected' to 'pending'.",
    "7. Next scrape run: workflow processes it and updates status automatically.",
    "",
    "ISSUE TYPES — column G",
    "incorrect-url",
    "  URL fetches the wrong product entirely.",
    "  corrected_url required. Workflow updates product_url in DB;",
    "  existing price history is kept (it was for the right product).",
    "",
    "wrong-format",
    "  URL is for wrong bottle size (magnum, half-bottle, case, wrong variant).",
    "  WITHOUT corrected_url: product deactivated, price records deleted.",
    "  WITH corrected_url: URL replaced, wrong-format records deleted,",
    "  product stays active and will be re-scraped with the new URL.",
    "",
    "404",
    "  Product removed or URL permanently broken.",
    "  WITHOUT corrected_url: product deactivated, price records deleted.",
    "  WITH corrected_url: same behaviour as wrong-format with replacement.",
    "",
    "validated-ok",
    "  Price flag is expected / a known anomaly — not a data error.",
    "  No DB change. Suppresses this (estate, retailer, vintage)",
    "  from Section 2 permanently. Delete this row to re-enable.",
    "",
    "wrong-estate / wrong-vintage / wrong-price / other",
    "  Cannot auto-fix. Marked needs-manual-action.",
    "  Update master_products.csv manually, commit with [skip ci].",
    "",
    "STATUS VALUES — column J",
    "auto-detected      Added by system from Section 2. Awaiting your review.",
    "pending            You confirmed issue. Workflow processes next run.",
    "processed          Workflow applied the fix. See processed_at (col K).",
    "validated          False positive confirmed. Suppressed from Section 2.",
    "                   Delete this row to re-enable the flag.",
    "needs-manual-action  Cannot auto-fix. Update CSV manually.",
    "error              Workflow error. Check GitHub Actions logs.",
    "",
    "FIELD REFERENCE — columns A to L",
    "A  date_flagged    Date the flag was created (auto-filled).",
    "B  source          'auto-detected' or 'manual'.",
    "C  estate_name     Estate name as in master_products.csv.",
    "D  retailer        Retailer key (e.g. vinotheque_bordeaux, millesima).",
    "E  vintage         Year as integer (blank = non-vintage).",
    "F  price           Price scraped that triggered the flag (auto-filled).",
    "G  current_url     URL currently tracked in master_products.",
    "H  issue_type      YOU fill in. See ISSUE TYPES above.",
    "I  corrected_url   YOU fill in — for incorrect-url (required);",
    "                   for wrong-format / 404 (optional, enables URL swap).",
    "J  notes           Auto-filled with detected issue. Add notes freely.",
    "K  status          Change to 'pending' to trigger processing.",
    "L  processed_at    Timestamp when workflow processed this row (auto-filled).",
    "",
    "IMPORTANT",
    "- Do not rename or reorder columns A-L.",
    "  The workflow locates columns by the header row.",
    "- wrong-format / 404 without corrected_url = product permanently",
    "  deactivated. If it comes back in stock, re-add to CSV and commit.",
    "- After processed rows: update master_products.csv to match, commit [skip ci].",
]


def process_flags() -> dict:
    """
    Read rows with status='pending' from the flags tab and apply DB corrections.
    Returns a summary dict passed to export_daily_report for Section 1.
    Called at the start of each run, before load_master_products and scraping.
    """
    summary = {"processed": 0, "needs_manual": 0, "errors": 0}

    client = _get_gsheet_client()
    sheet = client.open(GOOGLE_SHEET_NAME)

    try:
        ws = sheet.worksheet(FLAGS_TAB)
    except Exception:
        logger.info("No 'flags' tab found — skipping flag processing")
        return summary

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return summary

    header = all_rows[0]
    col = {name: idx for idx, name in enumerate(header)}

    if "status" not in col:
        logger.warning("flags tab missing 'status' column — skipping")
        return summary

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with engine.begin() as conn:
        for row_idx, row in enumerate(all_rows[1:], start=2):
            row = list(row) + [""] * (len(header) - len(row))

            if row[col["status"]].strip().lower() != "pending":
                continue

            issue_type    = row[col["issue_type"]].strip().lower()    if "issue_type"    in col else ""
            current_url   = row[col["current_url"]].strip()           if "current_url"   in col else ""
            corrected_url = row[col["corrected_url"]].strip()         if "corrected_url" in col else ""

            new_status = "processed"
            try:
                if issue_type == "incorrect-url":
                    if corrected_url:
                        _fix_url(conn, current_url, corrected_url)
                        summary["processed"] += 1
                    else:
                        new_status = "needs-manual-action"
                        summary["needs_manual"] += 1

                elif issue_type in ("wrong-format", "404"):
                    if not current_url:
                        new_status = "needs-manual-action"
                        summary["needs_manual"] += 1
                    elif corrected_url:
                        # Replacement known: swap URL, delete wrong records, keep product active
                        _replace_format(conn, current_url, corrected_url)
                        summary["processed"] += 1
                    else:
                        # No replacement: fully deactivate
                        _deactivate(conn, current_url)
                        summary["processed"] += 1

                elif issue_type == "validated-ok":
                    new_status = "validated"
                    summary["processed"] += 1

                else:
                    new_status = "needs-manual-action"
                    summary["needs_manual"] += 1

            except Exception as exc:
                logger.error(f"Flag row {row_idx} failed: {exc}")
                new_status = "error"
                summary["errors"] += 1

            ws.update_cell(row_idx, col["status"] + 1, new_status)
            if "processed_at" in col:
                ws.update_cell(row_idx, col["processed_at"] + 1, now_str)

    logger.info(f"Flags: {summary}")
    return summary


def _fix_url(conn, old_url: str, new_url: str) -> None:
    """incorrect-url: update URL in DB, keep existing price history (right product, wrong URL)."""
    conn.execute(
        text("UPDATE master_products SET product_url = :new WHERE product_url = :old"),
        {"new": new_url, "old": old_url},
    )
    conn.execute(
        text("UPDATE price_records SET url = :new WHERE url = :old"),
        {"new": new_url, "old": old_url},
    )
    logger.info(f"URL corrected: {old_url} -> {new_url}")


def _replace_format(conn, old_url: str, new_url: str) -> None:
    """
    wrong-format/404 with replacement: swap URL, delete wrong-format records,
    keep product active. Unlike _fix_url, records are deleted because they tracked
    prices for the wrong bottle size — that data is not valid price history.
    """
    mp = conn.execute(
        text("SELECT id, estate_name FROM master_products WHERE product_url = :url"),
        {"url": old_url},
    ).fetchone()
    if not mp:
        logger.warning(f"No master_product found for URL: {old_url}")
        return
    n = conn.execute(
        text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id"),
        {"id": mp.id},
    ).scalar()
    conn.execute(
        text("UPDATE master_products SET product_url = :new WHERE id = :id"),
        {"new": new_url, "id": mp.id},
    )
    conn.execute(
        text("DELETE FROM price_records WHERE master_product_id = :id"),
        {"id": mp.id},
    )
    logger.info(
        f"Format corrected: {mp.estate_name} (id={mp.id}) -> {new_url}, "
        f"deleted {n} wrong-format records, product stays active"
    )


def _deactivate(conn, url: str) -> None:
    """wrong-format/404 without replacement: deactivate product, delete all records."""
    mp = conn.execute(
        text("SELECT id, estate_name FROM master_products WHERE product_url = :url"),
        {"url": url},
    ).fetchone()
    if not mp:
        logger.warning(f"No master_product found for URL: {url}")
        return
    n = conn.execute(
        text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id"),
        {"id": mp.id},
    ).scalar()
    conn.execute(
        text("UPDATE master_products SET active = FALSE WHERE id = :id"),
        {"id": mp.id},
    )
    conn.execute(
        text("DELETE FROM price_records WHERE master_product_id = :id"),
        {"id": mp.id},
    )
    logger.info(f"Deactivated {mp.estate_name} (id={mp.id}), deleted {n} price_records")
