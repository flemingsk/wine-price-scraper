import os
import json
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from src.db import engine

GOOGLE_SHEET_NAME = "Wine Prices"
TAB_NAME = "price_records"


def export_to_gsheet():

    # Load credentials directly from env var — no temp file needed
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
    client = gspread.authorize(creds)

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
