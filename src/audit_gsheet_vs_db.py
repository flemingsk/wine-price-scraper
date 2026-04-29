"""
Audit: flag discrepancies between the Google Sheet and the Supabase DB.

Checks:
1. Price records in GSheet missing from DB (orphaned GSheet rows)
2. Price records in DB not exported to GSheet (export gaps)
3. Price values that differ between GSheet and DB for the same record ID
4. Row counts per retailer/date in GSheet vs DB
"""

import os
import json
import pandas as pd
from datetime import datetime
from sqlalchemy import text
from src.db import engine
from src.export_to_gsheet import _get_gsheet_client, GOOGLE_SHEET_NAME, TAB_NAME


def audit():
    print(f"\n{'='*70}")
    print(f"GSHEET vs DB AUDIT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}\n")

    # --- Load GSheet data ---
    print("Loading GSheet price_records tab...")
    client = _get_gsheet_client()
    sheet  = client.open(GOOGLE_SHEET_NAME)
    ws     = sheet.worksheet(TAB_NAME)
    gsheet_data = ws.get_all_values()

    if not gsheet_data:
        print("ERROR: GSheet tab is empty.")
        return

    headers   = gsheet_data[0]
    gsheet_df = pd.DataFrame(gsheet_data[1:], columns=headers)

    # Remap GSheet column names to match DB schema
    gsheet_df = gsheet_df.rename(columns={
        "data_id":   "id",
        "Reference": "estate_name",
        "Retailer":  "site",
        "Link":      "url",
        "Price":     "price_amount",
        "Unit":      "currency",
        "Timestamp": "fetched_at",
    })

    gsheet_df = gsheet_df[gsheet_df["id"] != ""]   # drop blank id rows
    gsheet_df["id"] = gsheet_df["id"].astype(str)

    print(f"GSheet rows (excluding header): {len(gsheet_df)}")

    # --- Load DB data ---
    print("Loading DB price_records...")
    db_df = pd.read_sql("""
        SELECT
            pr.id::text,
            mp.estate_name,
            pr.site,
            pr.vintage,
            pr.price_amount,
            pr.currency,
            pr.availability,
            pr.wine_color,
            DATE(pr.fetched_at) AS fetch_date
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        ORDER BY pr.id
    """, engine)
    db_df["id"] = db_df["id"].astype(str)

    print(f"DB rows: {len(db_df)}")

    # --- 1. IDs in GSheet but not in DB ---
    gsheet_ids = set(gsheet_df["id"])
    db_ids     = set(db_df["id"])

    in_gsheet_not_db = gsheet_ids - db_ids
    in_db_not_gsheet = db_ids - gsheet_ids

    print(f"\n1. IDs in GSheet but NOT in DB (phantom rows): {len(in_gsheet_not_db)}")
    if in_gsheet_not_db:
        sample = sorted(in_gsheet_not_db)[:10]
        for id_ in sample:
            row = gsheet_df[gsheet_df["id"] == id_].iloc[0]
            print(f"   id={id_} | {row.get('estate_name','?')} | {row.get('site','?')} | {row.get('fetched_at','?')[:10]}")
        if len(in_gsheet_not_db) > 10:
            print(f"   ... and {len(in_gsheet_not_db)-10} more")

    print(f"\n2. IDs in DB but NOT in GSheet (export gaps): {len(in_db_not_gsheet)}")
    if in_db_not_gsheet:
        # Summarise by date rather than listing all
        missing_rows = db_df[db_df["id"].isin(in_db_not_gsheet)]
        by_date = missing_rows.groupby("fetch_date").size().sort_index()
        print("   Missing by date:")
        for date, count in by_date.tail(10).items():
            print(f"   {date}: {count} rows missing")
        if len(by_date) > 10:
            print(f"   ... and {len(by_date)-10} earlier dates")

    # --- 3. Price value mismatches for shared IDs ---
    shared_ids = gsheet_ids & db_ids
    gsheet_prices = (
        gsheet_df[gsheet_df["id"].isin(shared_ids)][["id","price_amount"]]
        .rename(columns={"price_amount": "gsheet_price"})
    )
    db_prices = (
        db_df[db_df["id"].isin(shared_ids)][["id","price_amount"]]
        .rename(columns={"price_amount": "db_price"})
    )

    merged = gsheet_prices.merge(db_prices, on="id")
    merged["gsheet_price"] = pd.to_numeric(merged["gsheet_price"], errors="coerce")
    merged["db_price"]     = pd.to_numeric(merged["db_price"],     errors="coerce")
    merged["diff"]         = (merged["gsheet_price"] - merged["db_price"]).abs()

    mismatches = merged[merged["diff"] > 0.01].sort_values("diff", ascending=False)
    print(f"\n3. Price value mismatches (same ID, price differs): {len(mismatches)}")
    if not mismatches.empty:
        for _, row in mismatches.head(10).iterrows():
            db_row = db_df[db_df["id"] == row["id"]].iloc[0]
            print(f"   id={row['id']} | {db_row['estate_name']} | {db_row['site']} | "
                  f"GSheet={row['gsheet_price']:.2f} DB={row['db_price']:.2f} diff={row['diff']:.2f}")
        if len(mismatches) > 10:
            print(f"   ... and {len(mismatches)-10} more")

    # --- 4. Row-count summary ---
    print(f"\n4. SUMMARY")
    print(f"   GSheet rows:          {len(gsheet_df):>6}")
    print(f"   DB rows:              {len(db_df):>6}")
    print(f"   Shared IDs:           {len(shared_ids):>6}")
    print(f"   GSheet-only (orphan): {len(in_gsheet_not_db):>6}")
    print(f"   DB-only (not synced): {len(in_db_not_gsheet):>6}")
    print(f"   Price mismatches:     {len(mismatches):>6}")

    return {
        "gsheet_rows": len(gsheet_df),
        "db_rows": len(db_df),
        "shared": len(shared_ids),
        "gsheet_only": len(in_gsheet_not_db),
        "db_only": len(in_db_not_gsheet),
        "price_mismatches": len(mismatches),
    }


if __name__ == "__main__":
    # Load local gspread credentials if not in env
    if not os.getenv("GSHEET_CREDENTIALS_JSON"):
        try:
            with open("gspread_key.json") as f:
                os.environ["GSHEET_CREDENTIALS_JSON"] = f.read()
        except FileNotFoundError:
            pass
    audit()
