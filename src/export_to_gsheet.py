import os
import json
import pandas as pd
import gspread
from datetime import datetime, timedelta, timezone
from oauth2client.service_account import ServiceAccountCredentials
from src.db import engine

GOOGLE_SHEET_NAME = "Wine Prices"
TAB_NAME = "price_records"
REPORT_TAB_NAME = "daily_reports"
MARKET_ANALYSIS_TAB = "market_analysis"
RUN_LOG_TAB = "run_log"


def _get_gsheet_client():
    """Get authenticated gspread client."""
    gsheet_credentials_json = os.getenv("GSHEET_CREDENTIALS_JSON")
    if not gsheet_credentials_json:
        raise EnvironmentError(
            "GSHEET_CREDENTIALS_JSON environment variable is not set. "
            "Add the service account JSON as a GitHub Secret."
        )

    try:
        creds_dict = json.loads(gsheet_credentials_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"GSHEET_CREDENTIALS_JSON is not valid JSON: {e}")

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)


def export_daily_report():
    """Generate and append daily summary report to GSheet."""
    client = _get_gsheet_client()
    sheet = client.open(GOOGLE_SHEET_NAME)

    try:
        worksheet = sheet.worksheet(REPORT_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sheet.add_worksheet(title=REPORT_TAB_NAME, rows=1000, cols=10)

    # Use UTC to match how fetched_at is stored
    today = datetime.now(timezone.utc).date()

    try:
        summary = pd.read_sql(
            """
            SELECT
                COUNT(DISTINCT pr.id)                                          AS total_records,
                COUNT(DISTINCT pr.master_product_id)                           AS unique_products,
                SUM(CASE WHEN pr.price_corrected THEN 1 ELSE 0 END)::int       AS corrected_count
            FROM price_records pr
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = %s
            """,
            engine, params=[today],
        )
        corrections = pd.read_sql(
            """
            SELECT
                mp.estate_name,
                pr.site,
                pr.vintage,
                pr.original_price,
                pr.price_amount   AS corrected_price,
                pr.correction_reason,
                pr.url,
                pr.fetched_at
            FROM price_records pr
            JOIN master_products mp ON mp.id = pr.master_product_id
            WHERE DATE(pr.fetched_at AT TIME ZONE 'UTC') = %s
              AND pr.price_corrected = TRUE
            ORDER BY pr.fetched_at DESC
            """,
            engine, params=[today],
        )
    except Exception as exc:
        print(f"export_daily_report: DB query failed for {today}: {exc}")
        return

    rows_to_append = []

    all_values  = worksheet.get_all_values()
    needs_header = len(all_values) == 0
    if needs_header:
        rows_to_append.append(["Date", "Type", "Metric", "Value", "Details", "", "", "", ""])

    if not summary.empty:
        r = summary.iloc[0]
        row = [
            str(today), "SUMMARY",
            f"Records: {int(r['total_records'])}",
            f"Products: {int(r['unique_products'])}",
            f"Corrected: {int(r['corrected_count'])}",
            "", "", "", "",
        ]
        rows_to_append.append(row)

    if not corrections.empty:
        rows_to_append.append(["", "CORRECTIONS", "Estate", "Retailer", "Vintage", "Original", "Corrected", "Reason", "URL"])
        for _, corr in corrections.iterrows():
            rows_to_append.append([
                "", "",
                corr["estate_name"],
                corr["site"],
                str(corr["vintage"]),
                f"{corr['original_price']:.2f}" if corr["original_price"] else "",
                f"{corr['corrected_price']:.2f}" if corr["corrected_price"] else "",
                corr["correction_reason"] or "",
                corr["url"],
            ])

    if len(rows_to_append) == (1 if needs_header else 0):
        print(f"No report data for {today}.")
        return

    worksheet.append_rows(rows_to_append, value_input_option="USER_ENTERED")
    print(f"Appended daily report for {today} ({len(rows_to_append)} rows).")


def export_to_gsheet():
    client = _get_gsheet_client()

    # --------------------
    # OPEN SHEET
    # --------------------
    sheet = client.open(GOOGLE_SHEET_NAME)
    worksheet = sheet.worksheet(TAB_NAME)

    # Use get_all_values() — row_count returns allocated grid size (1000), not data rows
    all_values = worksheet.get_all_values()

    if len(all_values) == 0:
        existing_ids = set()
        needs_header = True
    else:
        existing_ids = set(row[0] for row in all_values[1:] if row and row[0])
        needs_header = False

    # --------------------
    # DB QUERY
    # --------------------
    query = """
    SELECT
        pr.id,
        mp.estate_name,
        pr.site,
        pr.url,
        pr.price_amount,
        pr.currency,
        pr.availability,
        pr.vintage,
        pr.wine_color,
        pr.fetched_at,
        pr.price_corrected,
        pr.original_price,
        pr.correction_reason
    FROM price_records pr
    JOIN master_products mp ON mp.id = pr.master_product_id
    ORDER BY pr.fetched_at;
    """
    df = pd.read_sql(query, engine)

    df["id"] = df["id"].astype(str)
    new_rows = df[~df["id"].isin(existing_ids)]

    if new_rows.empty:
        print("No new rows to append.")
        return

    if needs_header:
        worksheet.append_row(list(new_rows.columns))

    new_rows = new_rows.where(pd.notnull(new_rows), "")
    new_rows = new_rows.map(
        lambda x: x.isoformat() if hasattr(x, "isoformat") else x
    )

    worksheet.append_rows(
        new_rows.values.tolist(),
        value_input_option="USER_ENTERED",
    )

    print(f"Appended {len(new_rows)} new rows to Google Sheet.")


def export_market_analysis(lookback_days: int = 7):
    """
    Run market analysis and write results to the 'market_analysis' tab.

    Sections written (each preceded by a bold header row):
      1. Price Trends       — top movers (direction, % change, price range)
      2. Retailer Spreads   — cheapest vs most expensive per product
      3. Availability Changes — delistings and relistings
      4. Price Outliers     — statistical anomalies (Z-score > 2)

    The tab is completely refreshed on every call (clear + rewrite).
    """
    from src.market_analysis_reports import MarketAnalyzer

    analyzer = MarketAnalyzer(lookback_days=lookback_days)
    try:
        trends     = analyzer.calculate_price_trends()
        spreads    = analyzer.calculate_retailer_spreads()
        avail      = analyzer.calculate_availability_changes()
        outliers   = analyzer.identify_price_outliers()
    finally:
        analyzer.close()

    client    = _get_gsheet_client()
    sheet     = client.open(GOOGLE_SHEET_NAME)

    try:
        ws = sheet.worksheet(MARKET_ANALYSIS_TAB)
    except Exception:
        ws = sheet.add_worksheet(title=MARKET_ANALYSIS_TAB, rows=500, cols=10)
    ws.clear()

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = [
        [f"Market Analysis — generated {generated} — lookback {lookback_days}d"],
        [],
    ]

    # --- 1. Price Trends ---
    rows.append(["PRICE TRENDS", "Color", "Estate", "Vintage", "Retailer", "Direction", "Change %", "First Price", "Last Price", "Days of Data"])
    for t in trends[:30]:
        rows.append([
            "",
            t["wine_color"],
            t["estate"],
            str(t["vintage"] or ""),
            t["retailer"],
            t["direction"],
            f"{t['change_pct']:+.1f}%",
            f"€{t['first_price']:.2f}",
            f"€{t['last_price']:.2f}",
            t["days_of_data"],
        ])
    rows.append([])

    # --- 2. Retailer Spreads ---
    rows.append(["RETAILER SPREADS", "Color", "Estate", "Vintage", "Spread %", "Min Price", "Cheapest", "Max Price", "Most Expensive", "# Retailers"])
    for s in spreads[:30]:
        rows.append([
            "",
            s["wine_color"],
            s["estate"],
            str(s["vintage"] or ""),
            f"{s['spread_pct']:.1f}%",
            f"€{s['min_price']:.2f}",
            s["cheapest"],
            f"€{s['max_price']:.2f}",
            s["most_expensive"],
            s["num_retailers"],
        ])
    rows.append([])

    # --- 3. Availability Changes ---
    rows.append(["AVAILABILITY CHANGES", "Color", "Estate", "Vintage", "Retailer", "Change", "Date Range"])
    if avail:
        for a in avail:
            rows.append([
                "",
                a["wine_color"],
                a["estate"],
                str(a["vintage"] or ""),
                a["retailer"],
                a["status_change"],
                a["date_range"],
            ])
    else:
        rows.append(["", "", "(no changes in period)"])
    rows.append([])

    # --- 4. Price Outliers ---
    rows.append(["PRICE OUTLIERS", "Color", "Estate", "Vintage", "Retailer", "Outlier Price", "Expected Price", "Deviation %", "Z-Score"])
    for o in outliers[:20]:
        rows.append([
            "",
            o["wine_color"],
            o["estate"],
            str(o["vintage"] or ""),
            o["retailer"],
            f"€{o['outlier_price']:.2f}",
            f"€{o['expected_price']:.2f}",
            f"{o['deviation_pct']:+.1f}%",
            f"{o['z_score']:.1f}",
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    # Bold the section header rows
    header_rows = [1, 3]  # "Market Analysis..." and "PRICE TRENDS"
    for i, row in enumerate(rows, start=1):
        if row and row[0] in ("PRICE TRENDS", "RETAILER SPREADS", "AVAILABILITY CHANGES", "PRICE OUTLIERS"):
            header_rows.append(i)

    for row_idx in header_rows:
        try:
            ws.format(f"A{row_idx}:J{row_idx}", {"textFormat": {"bold": True}})
        except Exception:
            pass  # formatting is cosmetic — don't fail the export

    print(
        f"Market analysis exported to '{GOOGLE_SHEET_NAME}' > '{MARKET_ANALYSIS_TAB}': "
        f"{len(trends)} trends, {len(spreads)} spreads, {len(avail)} availability changes, {len(outliers)} outliers"
    )


def export_run_log(start_time: datetime, duration_seconds: float) -> None:
    """
    Append one run entry to the 'run_log' GSheet tab.

    Each entry consists of:
      - 1 RUN summary row  (date/time, duration, counts)
      - N MISSING rows     (products available last run but absent today, with URL)
      - N PRICE CHANGE rows (price moved since last run, with prev/new/Δ%)
    """
    client = _get_gsheet_client()
    sheet = client.open(GOOGLE_SHEET_NAME)

    try:
        ws = sheet.worksheet(RUN_LOG_TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet(title=RUN_LOG_TAB, rows=5000, cols=15)

    today = start_time.date()
    run_ts = start_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    mins, secs = divmod(int(duration_seconds), 60)
    duration_str = f"{mins}m {secs}s"

    # ── 1. Master product count (active 75cl rows — mirrors CSV) ─────────────
    master_df = pd.read_sql(
        "SELECT COUNT(*) AS cnt FROM master_products "
        "WHERE active = TRUE AND bottle_size IN ('0.75L','75cl')",
        engine,
    )
    master_count = int(master_df.iloc[0]["cnt"])

    # ── 2. Successfully scraped today ─────────────────────────────────────────
    scraped_df = pd.read_sql(
        "SELECT COUNT(DISTINCT master_product_id) AS cnt "
        "FROM price_records WHERE DATE(fetched_at) = %s AND price_amount IS NOT NULL",
        engine, params=[today],
    )
    scraped_count = int(scraped_df.iloc[0]["cnt"])

    # ── 3. Corrected today ───────────────────────────────────────────────────
    corrected_df = pd.read_sql(
        "SELECT COUNT(*) AS cnt FROM price_records "
        "WHERE DATE(fetched_at) = %s AND price_corrected = TRUE",
        engine, params=[today],
    )
    corrected_count = int(corrected_df.iloc[0]["cnt"])

    # ── 4. Missing: had a price in the previous run date, absent today ────────
    missing_df = pd.read_sql(
        """
        WITH prev_date AS (
            SELECT MAX(DATE(fetched_at)) AS d
            FROM price_records
            WHERE DATE(fetched_at) < %(today)s AND price_amount IS NOT NULL
        ),
        prev_run AS (
            SELECT DISTINCT ON (pr.master_product_id, pr.vintage)
                pr.master_product_id,
                pr.vintage,
                pr.url,
                DATE(pr.fetched_at) AS last_seen
            FROM price_records pr
            CROSS JOIN prev_date
            WHERE DATE(pr.fetched_at) = prev_date.d AND pr.price_amount IS NOT NULL
            ORDER BY pr.master_product_id, pr.vintage
        ),
        today_run AS (
            SELECT DISTINCT master_product_id, vintage
            FROM price_records
            WHERE DATE(fetched_at) = %(today)s AND price_amount IS NOT NULL
        )
        SELECT
            mp.estate_name,
            COALESCE(prev_run.vintage::text, 'NV') AS vintage,
            mp.retailer,
            prev_run.url,
            prev_run.last_seen
        FROM prev_run
        JOIN master_products mp ON mp.id = prev_run.master_product_id
        LEFT JOIN today_run
               ON today_run.master_product_id = prev_run.master_product_id
              AND today_run.vintage           = prev_run.vintage
        WHERE today_run.master_product_id IS NULL
        ORDER BY mp.estate_name, mp.retailer
        """,
        engine, params={"today": today},
    )

    # ── 5. Price changes since previous run ──────────────────────────────────
    price_changes_df = pd.read_sql(
        """
        WITH prev_date AS (
            SELECT MAX(DATE(fetched_at)) AS d
            FROM price_records
            WHERE DATE(fetched_at) < %(today)s AND price_amount IS NOT NULL
        ),
        prev_prices AS (
            SELECT DISTINCT ON (pr.master_product_id, pr.vintage)
                pr.master_product_id,
                pr.vintage,
                pr.price_amount
            FROM price_records pr
            CROSS JOIN prev_date
            WHERE DATE(pr.fetched_at) = prev_date.d AND pr.price_amount IS NOT NULL
            ORDER BY pr.master_product_id, pr.vintage, pr.fetched_at DESC
        ),
        today_prices AS (
            SELECT DISTINCT ON (master_product_id, vintage)
                master_product_id,
                vintage,
                price_amount
            FROM price_records
            WHERE DATE(fetched_at) = %(today)s AND price_amount IS NOT NULL
            ORDER BY master_product_id, vintage, fetched_at DESC
        )
        SELECT
            mp.estate_name,
            COALESCE(tp.vintage::text, 'NV') AS vintage,
            mp.retailer,
            pp.price_amount AS prev_price,
            tp.price_amount AS new_price,
            ROUND(
                ((tp.price_amount - pp.price_amount) / pp.price_amount * 100)::numeric,
                1
            ) AS pct_change
        FROM today_prices tp
        JOIN prev_prices   pp ON pp.master_product_id = tp.master_product_id
                              AND pp.vintage           = tp.vintage
        JOIN master_products mp ON mp.id = tp.master_product_id
        WHERE tp.price_amount <> pp.price_amount
        ORDER BY ABS((tp.price_amount - pp.price_amount) / pp.price_amount) DESC
        """,
        engine, params={"today": today},
    )

    missing_count      = len(missing_df)
    price_change_count = len(price_changes_df)

    # ── Build rows ────────────────────────────────────────────────────────────
    all_values  = ws.get_all_values()
    needs_header = len(all_values) == 0

    rows_to_append = []
    if needs_header:
        rows_to_append.append([
            "Run Date/Time (UTC)", "Duration",
            "Master Products", "Scraped", "Corrected",
            "Missing vs Prev Run", "Price Changes",
            "Type", "Estate", "Vintage", "Retailer",
            "URL / Detail", "Prev €", "New €", "Δ%",
        ])

    # Summary row
    rows_to_append.append([
        run_ts, duration_str,
        master_count, scraped_count, corrected_count,
        missing_count, price_change_count,
        "RUN", "", "", "", "", "", "", "",
    ])

    # Missing-product detail rows
    for _, row in missing_df.iterrows():
        rows_to_append.append([
            "", "", "", "", "", "", "",
            "MISSING",
            row["estate_name"],
            str(row["vintage"]),
            row["retailer"],
            f"Last seen {row['last_seen']} | {row['url']}",
            "", "", "",
        ])

    # Price-change detail rows
    for _, row in price_changes_df.iterrows():
        rows_to_append.append([
            "", "", "", "", "", "", "",
            "PRICE CHANGE",
            row["estate_name"],
            str(row["vintage"]),
            row["retailer"],
            "",
            f"€{float(row['prev_price']):.2f}",
            f"€{float(row['new_price']):.2f}",
            f"{float(row['pct_change']):+.1f}%",
        ])

    ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
    print(
        f"Run log: {run_ts} | {scraped_count}/{master_count} scraped | "
        f"{missing_count} missing | {price_change_count} price changes | {duration_str}"
    )


if __name__ == "__main__":
    export_to_gsheet()
