"""
fix_estate_names.py — one-time audit + fix for wrong estate_name values in master_products.

Run locally:
    python fix_estate_names.py [--dry-run]

What it does:
  1. Prints every distinct estate_name in the DB so you can spot anything odd.
  2. Applies CANONICAL_NAMES: renames wrong-named rows and, when a correctly-named
     row already exists for the same (retailer, vintage, bottle_size), merges the
     price_records into the correct row (skipping exact-day duplicates) then deletes
     the orphan.
  3. Clears the GSheet price_records tab so the next scrape run re-exports everything
     with correct estate names.

Safe to re-run: all CANONICAL_NAMES fixes are idempotent.
Add --dry-run to print what would change without touching the DB or GSheet.
"""
from __future__ import annotations
import sys
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from src.db import engine

DRY_RUN = "--dry-run" in sys.argv

# ── Canonical name mapping ────────────────────────────────────────────────────
# Maps any known wrong name -> the correct canonical name.
# When you discover a new inconsistency, add it here and re-run.
CANONICAL_NAMES: dict[str, str] = {
    "Chateau Malartic Lagraviere":  "Chateau Malartic-Lagraviere",
    "Chateau Sociando Mallet":      "Chateau Sociando-Mallet",
    # 'c' was a CSV typo for Larrivet Haut-Brion Blanc 2022 jean_merlaut
    "c":                            "Chateau Larrivet Haut-Brion",
}

# URL-based overrides: used when the wrong name is too generic to put in CANONICAL_NAMES
# (e.g. "c" could theoretically be anything — we pin it by URL).
# Maps product_url -> correct estate_name.
URL_OVERRIDES: dict[str, str] = {
    "https://jean-merlaut.com/catalogue-des-vins/3533-chateau-larrivet-haut-brion-blanc-2022.html":
        "Chateau Larrivet Haut-Brion",
}


def _merge_or_rename(conn, wrong_id: int, wrong_name: str, correct_name: str, ctx: str) -> None:
    """
    If a correctly-named row exists for the same retailer/vintage/bottle_size:
      - Move price_records from wrong row to correct row (skip date duplicates)
      - Delete remaining price_records on wrong row (true duplicates)
      - Delete wrong master_products row
    Else:
      - Simply rename the wrong row's estate_name in place.
    """
    wrong_meta = conn.execute(
        text("SELECT retailer, vintage_start, bottle_size FROM master_products WHERE id = :id"),
        {"id": wrong_id},
    ).fetchone()
    if not wrong_meta:
        print(f"  [SKIP] id={wrong_id} not found (already deleted?)")
        return

    correct_row = conn.execute(
        text("""
            SELECT id FROM master_products
            WHERE estate_name = :name AND retailer = :ret
              AND vintage_start IS NOT DISTINCT FROM :vs
              AND bottle_size = :bs
        """),
        {"name": correct_name, "ret": wrong_meta.retailer,
         "vs": wrong_meta.vintage_start, "bs": wrong_meta.bottle_size},
    ).fetchone()

    n_records = conn.execute(
        text("SELECT COUNT(*) FROM price_records WHERE master_product_id = :id"),
        {"id": wrong_id},
    ).scalar()

    if correct_row:
        correct_id = correct_row.id
        print(f"  MERGE  {ctx}: wrong id={wrong_id} ({n_records} records) -> correct id={correct_id}")
        if not DRY_RUN:
            moved = conn.execute(text("""
                UPDATE price_records pr
                SET master_product_id = :cid
                WHERE master_product_id = :wid
                  AND NOT EXISTS (
                    SELECT 1 FROM price_records pr2
                    WHERE pr2.master_product_id = :cid
                      AND pr2.vintage IS NOT DISTINCT FROM pr.vintage
                      AND DATE(pr2.fetched_at AT TIME ZONE 'UTC')
                          = DATE(pr.fetched_at AT TIME ZONE 'UTC')
                  )
            """), {"cid": correct_id, "wid": wrong_id}).rowcount
            dupes = conn.execute(
                text("DELETE FROM price_records WHERE master_product_id = :id"),
                {"id": wrong_id},
            ).rowcount
            conn.execute(
                text("DELETE FROM master_products WHERE id = :id"),
                {"id": wrong_id},
            )
            print(f"         moved={moved}, duplicate records dropped={dupes}, orphan row deleted")
    else:
        print(f"  RENAME {ctx}: id={wrong_id} '{wrong_name}' -> '{correct_name}' ({n_records} records kept)")
        if not DRY_RUN:
            conn.execute(
                text("UPDATE master_products SET estate_name = :name WHERE id = :id"),
                {"name": correct_name, "id": wrong_id},
            )


def main() -> None:
    if DRY_RUN:
        print("=== DRY RUN — no changes will be made ===\n")

    with engine.begin() as conn:
        # ── 1. Audit: print all distinct estate names ─────────────────────────
        all_names = conn.execute(
            text("SELECT DISTINCT estate_name FROM master_products ORDER BY estate_name")
        ).fetchall()

        print(f"Distinct estate names in DB ({len(all_names)}):")
        for (name,) in all_names:
            canonical = CANONICAL_NAMES.get(name)
            flag = ""
            if canonical:
                flag = f"  -> CANONICAL FIX: '{canonical}'"
            elif len(name.strip()) < 5:
                flag = "  *** SUSPICIOUSLY SHORT ***"
            print(f"  {name!r}{flag}")
        print()

        # ── 2. Apply CANONICAL_NAMES fixes ────────────────────────────────────
        for wrong_name, correct_name in CANONICAL_NAMES.items():
            wrong_rows = conn.execute(
                text("SELECT id FROM master_products WHERE estate_name = :name"),
                {"name": wrong_name},
            ).fetchall()
            if not wrong_rows:
                print(f"[OK] '{wrong_name}' — not in DB, nothing to fix")
                continue
            print(f"\nFixing '{wrong_name}' -> '{correct_name}' ({len(wrong_rows)} rows):")
            for (wrong_id,) in wrong_rows:
                _merge_or_rename(conn, wrong_id, wrong_name, correct_name,
                                 f"'{wrong_name}' id={wrong_id}")

        # ── 3. Apply URL-based overrides ──────────────────────────────────────
        print()
        for url, correct_name in URL_OVERRIDES.items():
            rows = conn.execute(
                text("SELECT id, estate_name FROM master_products WHERE product_url = :url"),
                {"url": url},
            ).fetchall()
            for (mp_id, current_name) in rows:
                if current_name == correct_name:
                    print(f"[OK] URL override already correct: '{correct_name}' id={mp_id}")
                else:
                    print(f"\nURL override: id={mp_id} '{current_name}' -> '{correct_name}'")
                    _merge_or_rename(conn, mp_id, current_name, correct_name,
                                     f"URL override id={mp_id}")

        print("\nDB fix complete." if not DRY_RUN else "\nDry run complete — no changes made.")

    # ── 4. Clear GSheet price_records tab ─────────────────────────────────────
    print("\nClearing GSheet price_records tab for clean re-export...")
    if DRY_RUN:
        print("[DRY RUN] Would clear GSheet tab and re-export on next run.")
        return
    try:
        import os, json, gspread
        from oauth2client.service_account import ServiceAccountCredentials
        creds_json = os.getenv("GSHEET_CREDENTIALS_JSON")
        if not creds_json:
            print("GSHEET_CREDENTIALS_JSON not set — skipping GSheet clear.")
            print("Run the next scrape manually; it will re-export all records cleanly.")
            return
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
        client = gspread.authorize(creds)
        sheet = client.open("Wine Prices")
        ws = sheet.worksheet("price_records")
        ws.clear()
        print("GSheet price_records tab cleared. Next scrape run will re-export all records.")
    except Exception as exc:
        print(f"GSheet clear failed: {exc}")
        print("Manually clear the 'price_records' tab in the GSheet, or run the next scrape.")


if __name__ == "__main__":
    main()
