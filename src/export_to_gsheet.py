import os
import json
import pandas as pd
import gspread
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
from src.db import engine

GOOGLE_SHEET_NAME = "Wine Prices"
TAB_NAME = "price_records"
REPORT_TAB_NAME = "daily_reports"


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

    # Get or create report tab
    try:
        worksheet = sheet.worksheet(REPORT_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sheet.add_worksheet(title=REPORT_TAB_NAME, rows=1000, cols=10)

    # Get today's date
    today = datetime.now().date()

    # Query today's scrape results
    query = """
    SELECT
        DATE(pr.fetched_at) as scrape_date,
        COUNT(DISTINCT pr.id) as total_records,
        COUNT(DISTINCT pr.master_product_id) as unique_products,
        SUM(CASE WHEN pr.price_corrected THEN 1 ELSE 0 END) as corrected_count
    FROM price_records pr
    WHERE DATE(pr.fetched_at) = %s
    GROUP BY DATE(pr.fetched_at);
    """

    summary = pd.read_sql(query, engine, params=[today])

    # Query corrected entries with details
    corrected_query = """
    SELECT
        mp.estate_name,
        pr.site,
        pr.vintage,
        pr.original_price,
        pr.price_amount as corrected_price,
        pr.correction_reason,
        pr.fetched_at
    FROM price_records pr
    JOIN master_products mp ON mp.id = pr.master_product_id
    WHERE DATE(pr.fetched_at) = %s AND pr.price_corrected = TRUE
    ORDER BY pr.fetched_at DESC;
    """

    corrections = pd.read_sql(corrected_query, engine, params=[today])

    # Check if we have existing data
    all_values = worksheet.get_all_values()
    if len(all_values) == 0:
        needs_header = True
    else:
        needs_header = False

    # Build report rows
    report_rows = []

    if not summary.empty:
        summary_row = summary.iloc[0]
        report_rows.append([
            str(today),
            "SUMMARY",
            f"Records: {int(summary_row['total_records'])}",
            f"Products: {int(summary_row['unique_products'])}",
            f"Corrected: {int(summary_row['corrected_count'])}",
        ])

    if not corrections.empty:
        report_rows.append(["", "CORRECTIONS", "Estate", "Retailer", "Vintage", "Original", "Corrected", "Reason"])
        for _, corr in corrections.iterrows():
            report_rows.append([
                "",
                "",
                corr["estate_name"],
                corr["site"],
                str(corr["vintage"]),
                f"{corr['original_price']:.2f}",
                f"{corr['corrected_price']:.2f}",
                corr["correction_reason"],
            ])

    if not report_rows:
        print(f"No report data for {today}.")
        return

    if needs_header:
        header = ["Date", "Type", "Metric", "Value", "Details", "", "", ""]
        worksheet.append_row(header)

    for row in report_rows:
        # Pad row to 8 columns
        while len(row) < 8:
            row.append("")
        worksheet.append_row(row, value_input_option="USER_ENTERED")

    print(f"Appended daily report for {today} ({len(report_rows)} rows).")


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
        pr.fetched_at
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


if __name__ == "__main__":
    export_to_gsheet()
