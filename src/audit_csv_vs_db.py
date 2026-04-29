"""
Audit: compare the locally-downloaded GSheet CSV against the Supabase DB.

Usage:
    py -m src.audit_csv_vs_db                       # default CSV filename
    py -m src.audit_csv_vs_db --csv path/to/file.csv

Checks:
  1. IDs in GSheet CSV but missing from DB (phantom / deleted rows)
  2. IDs in DB but not yet exported to GSheet (export gaps, by date)
  3. Price mismatches for shared IDs — GSheet value differs from DB value
  4. High-price DB records (>80 EUR) that may still be uncorrected case prices
"""

import argparse
import csv
import os
import sys
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from src.db import engine  # noqa: E402 — needs env loaded first

DEFAULT_CSV = "Wine Prices - price_records.csv"


def load_gsheet_csv(path: str) -> pd.DataFrame:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        sys.exit(f"ERROR: CSV is empty — {path}")

    df = pd.DataFrame(rows)

    # Normalise column names to match DB schema
    rename = {
        "data_id":   "id",
        "Reference": "estate_name",
        "Retailer":  "site",
        "Link":      "url",
        "Price":     "price_amount",
        "Unit":      "currency",
        "Note":      "note",
        "Timestamp": "fetched_at",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    df = df[df["id"] != ""]
    df["id"] = df["id"].astype(str).str.strip()
    df["price_amount"] = pd.to_numeric(
        df["price_amount"].str.replace(",", "."), errors="coerce"
    )
    return df


def load_db() -> pd.DataFrame:
    return pd.read_sql(
        """
        SELECT
            pr.id::text,
            mp.estate_name,
            pr.site,
            pr.vintage,
            pr.price_amount::float,
            pr.currency,
            pr.wine_color,
            pr.price_corrected,
            pr.original_price::float,
            pr.correction_reason,
            DATE(pr.fetched_at) AS fetch_date
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        ORDER BY pr.id
        """,
        engine,
    )


def run_audit(csv_path: str):
    print(f"\n{'='*70}")
    print(f"CSV vs DB AUDIT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"CSV: {csv_path}")
    print(f"{'='*70}\n")

    print("Loading GSheet CSV...")
    gs = load_gsheet_csv(csv_path)
    print(f"  GSheet rows: {len(gs)}")

    print("Loading DB...")
    db = load_db()
    print(f"  DB rows:     {len(db)}")

    gs_ids = set(gs["id"])
    db_ids = set(db["id"])

    phantom    = gs_ids - db_ids
    not_synced = db_ids - gs_ids
    shared     = gs_ids & db_ids

    # ── 1. Phantom rows ──────────────────────────────────────────────────────
    print(f"\n1. IDs in GSheet but NOT in DB (phantom/deleted rows): {len(phantom)}")
    if phantom:
        sample = sorted(phantom)[:10]
        for id_ in sample:
            row = gs[gs["id"] == id_].iloc[0]
            print(f"   id={id_} | {row.get('estate_name','?')} | {row.get('site','?')} | {str(row.get('fetched_at','?'))[:10]}")
        if len(phantom) > 10:
            print(f"   ... and {len(phantom)-10} more")

    # ── 2. Export gaps ────────────────────────────────────────────────────────
    print(f"\n2. IDs in DB but NOT in GSheet (export gaps): {len(not_synced)}")
    if not_synced:
        missing = db[db["id"].isin(not_synced)]
        by_date = missing.groupby("fetch_date").size().sort_index()
        print("   Missing by date (most recent first):")
        for date, count in by_date.tail(14).items():
            print(f"   {date}: {count} rows")
        if len(by_date) > 14:
            print(f"   ... and {len(by_date)-14} earlier dates")

    # ── 3. Price mismatches ──────────────────────────────────────────────────
    gs_prices = gs[gs["id"].isin(shared)][["id", "price_amount"]].rename(
        columns={"price_amount": "gsheet_price"}
    )
    db_prices = db[db["id"].isin(shared)][["id", "price_amount", "price_corrected",
                                            "original_price", "correction_reason",
                                            "estate_name", "site"]].rename(
        columns={"price_amount": "db_price"}
    )

    merged = gs_prices.merge(db_prices, on="id")
    merged["diff"] = (merged["gsheet_price"] - merged["db_price"]).abs()
    mismatches = merged[merged["diff"] > 0.01].sort_values("diff", ascending=False)

    print(f"\n3. Price mismatches (same ID, value differs): {len(mismatches)}")
    if not mismatches.empty:
        # Categorise: likely case-price corrections vs genuine edits
        case6  = mismatches[mismatches["diff"].apply(lambda d: any(abs(d - r["db_price"] * (n-1)) < 0.05 for n in [6, 12] for _, r in mismatches[mismatches["diff"] == d].iterrows()))]
        print("   Top mismatches (largest diff first):")
        for _, row in mismatches.head(20).iterrows():
            gs_p  = row["gsheet_price"]
            db_p  = row["db_price"]
            ratio = (db_p / gs_p) if gs_p else 0
            hint  = ""
            if abs(ratio - 6) < 0.1:
                hint = " ← likely 6-bottle case in DB"
            elif abs(ratio - 12) < 0.1:
                hint = " ← likely 12-bottle case in DB"
            corrected_flag = " [DB auto-corrected]" if row.get("price_corrected") else ""
            print(f"   id={row['id']} | {row['estate_name']} @ {row['site']}")
            print(f"     GSheet={gs_p:.2f}  DB={db_p:.2f}  ratio={ratio:.1f}x{hint}{corrected_flag}")
        if len(mismatches) > 20:
            print(f"   ... and {len(mismatches)-20} more")

    # ── 4. High-price DB records still uncorrected ───────────────────────────
    high_db = db[(db["db_price"] > 80) & (~db["price_corrected"].fillna(False))]
    print(f"\n4. DB records >80 EUR not auto-corrected: {len(high_db)}")
    if not high_db.empty:
        by_ret = high_db.groupby("site")["db_price"].agg(["count", "mean", "max"])
        print("   By retailer:")
        for ret, row in by_ret.sort_values("count", ascending=False).iterrows():
            print(f"   {ret:<22} n={int(row['count'])}  avg={row['mean']:.0f}  max={row['max']:.0f}")

        print("   Top individual records:")
        for _, row in high_db.nlargest(15, "db_price").iterrows():
            print(f"   {row['db_price']:>8.2f}  {row['site']:<22}  {row['estate_name']}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("SUMMARY")
    print(f"  GSheet rows:             {len(gs):>6}")
    print(f"  DB rows:                 {len(db):>6}")
    print(f"  Shared IDs:              {len(shared):>6}")
    print(f"  Phantom (GSheet only):   {len(phantom):>6}")
    print(f"  Not synced (DB only):    {len(not_synced):>6}")
    print(f"  Price mismatches:        {len(mismatches):>6}")
    print(f"  DB records >80 EUR (uncorrected): {len(high_db):>4}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=DEFAULT_CSV)
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"CSV file not found: {args.csv}")

    run_audit(args.csv)
