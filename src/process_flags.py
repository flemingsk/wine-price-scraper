# src/process_flags.py
"""
User-feedback processing: reads 'pending' rows from the 'flags' GSheet tab
and applies corrections to the DB before each scrape run.

Workflow:
  User spots an issue in the daily_report tab -> flags tab has a pre-populated
  auto-detected row, or user adds one manually -> sets issue_type + corrected_url
  -> sets status to 'pending' -> next run picks it up here.

Auto-fixable issue types (set status='pending' to trigger):
  incorrect-url  — updates product_url in master_products + url in price_records
  wrong-format   — deactivates master_product in DB, deletes price_records
                   (covers magnums, half-bottles, case prices, wrong-size variants)
  404            — same as wrong-format

Validated / false-positive suppression (set status='pending' to trigger):
  validated-ok   — marks status='validated'; suppresses (estate, retailer, vintage)
                   from appearing in Section 2 of future daily reports permanently.
                   Delete the row from the flags tab to re-enable the flag.

Needs-manual-action (workflow flags but cannot auto-fix):
  wrong-estate, wrong-vintage, wrong-price, duplicate, other

Note: DB changes are applied immediately. The master_products.csv stays out of
sync until updated manually — auto-committing CSV from CI creates a push loop.
Run a CSV sync after reviewing 'processed' rows in the flags tab.
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
    "date_flagged", "source", "estate_name", "retailer", "vintage",
    "current_url", "issue_type", "corrected_url", "notes", "status", "processed_at",
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
                    if current_url:
                        _deactivate(conn, current_url)
                        summary["processed"] += 1
                    else:
                        new_status = "needs-manual-action"
                        summary["needs_manual"] += 1

                elif issue_type == "validated-ok":
                    # No DB change — marks as validated so Section 2 suppresses this
                    # (estate, retailer, vintage) combination in future reports.
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
    conn.execute(
        text("UPDATE master_products SET product_url = :new WHERE product_url = :old"),
        {"new": new_url, "old": old_url},
    )
    conn.execute(
        text("UPDATE price_records SET url = :new WHERE url = :old"),
        {"new": new_url, "old": old_url},
    )
    logger.info(f"URL corrected: {old_url} -> {new_url}")


def _deactivate(conn, url: str) -> None:
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
