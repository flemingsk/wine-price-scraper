"""
_fix_null_vintages.py — fix NULL vintage values in price_records.

Run:  py _fix_null_vintages.py [--dry-run]

Strategy:
  1. Range products (vintage_start != vintage_end): the scraper looped over many
     vintages but stored NULL for each. We cannot determine which year each record
     belongs to → DELETE these records.
  2. Single-vintage products (vintage_start = vintage_end): each NULL record
     clearly belongs to that one year → UPDATE vintage = vintage_start.
  3. Any remaining NULLs (vintage_start also NULL): these are non-vintage wines
     that should have been stored as 0 → UPDATE vintage = 0.
"""
import sys
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from src.db import engine

DRY_RUN = "--dry-run" in sys.argv

with engine.begin() as conn:
    # ── Audit before ──────────────────────────────────────────────────────────
    total_null = conn.execute(text(
        "SELECT COUNT(*) FROM price_records WHERE vintage IS NULL"
    )).scalar()
    print(f"NULL-vintage records before fix: {total_null}")

    if total_null == 0:
        print("Nothing to fix.")
        raise SystemExit(0)

    # ── Step 1: DELETE range-product NULL records (ambiguous vintage) ─────────
    delete_sql = text("""
        DELETE FROM price_records pr
        USING master_products mp
        WHERE pr.master_product_id = mp.id
          AND pr.vintage IS NULL
          AND mp.vintage_start IS NOT NULL
          AND mp.vintage_end IS NOT NULL
          AND mp.vintage_start != mp.vintage_end
    """)
    if DRY_RUN:
        n_del = conn.execute(text("""
            SELECT COUNT(*) FROM price_records pr
            JOIN master_products mp ON pr.master_product_id = mp.id
            WHERE pr.vintage IS NULL
              AND mp.vintage_start IS NOT NULL
              AND mp.vintage_end IS NOT NULL
              AND mp.vintage_start != mp.vintage_end
        """)).scalar()
        print(f"  [DRY RUN] Would DELETE {n_del} range-product NULL records (ambiguous vintage)")
    else:
        result = conn.execute(delete_sql)
        print(f"  Deleted {result.rowcount} range-product NULL records (ambiguous vintage)")

    # ── Step 2: UPDATE single-vintage NULL records → vintage_start ────────────
    update_single_sql = text("""
        UPDATE price_records pr
        SET vintage = mp.vintage_start
        FROM master_products mp
        WHERE pr.master_product_id = mp.id
          AND pr.vintage IS NULL
          AND mp.vintage_start IS NOT NULL
          AND (mp.vintage_end IS NULL OR mp.vintage_end = mp.vintage_start)
    """)
    if DRY_RUN:
        n_upd = conn.execute(text("""
            SELECT COUNT(*) FROM price_records pr
            JOIN master_products mp ON pr.master_product_id = mp.id
            WHERE pr.vintage IS NULL
              AND mp.vintage_start IS NOT NULL
              AND (mp.vintage_end IS NULL OR mp.vintage_end = mp.vintage_start)
        """)).scalar()
        print(f"  [DRY RUN] Would UPDATE {n_upd} single-vintage NULL records to vintage_start")
    else:
        result = conn.execute(update_single_sql)
        print(f"  Updated {result.rowcount} single-vintage NULL records -> vintage_start")

    # ── Step 3: Remaining NULLs (NV wines where vintage_start is also NULL) ───
    update_nv_sql = text(
        "UPDATE price_records SET vintage = 0 WHERE vintage IS NULL"
    )
    if DRY_RUN:
        n_nv = conn.execute(text(
            "SELECT COUNT(*) FROM price_records WHERE vintage IS NULL"
        )).scalar()
        # Note: in dry-run the above steps didn't run, so this includes all of them
        print(f"  [DRY RUN] Would UPDATE remaining NULLs to 0 (non-vintage fallback)")
    else:
        result = conn.execute(update_nv_sql)
        if result.rowcount:
            print(f"  Updated {result.rowcount} remaining NULL records -> 0 (non-vintage)")

    # ── Audit after ───────────────────────────────────────────────────────────
    if not DRY_RUN:
        remaining = conn.execute(text(
            "SELECT COUNT(*) FROM price_records WHERE vintage IS NULL"
        )).scalar()
        print(f"\nNULL-vintage records after fix: {remaining}")
        if remaining == 0:
            print("All vintage values are now non-NULL.")

if DRY_RUN:
    print("\nDry run complete. Re-run without --dry-run to apply.")
