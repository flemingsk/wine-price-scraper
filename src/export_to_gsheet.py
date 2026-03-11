import os
import json
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from src.db import engine

# --------------------
# CONFIG
# --------------------
GOOGLE_SHEET_NAME = "Wine Prices"
TAB_NAME = "price_records"
SERVICE_ACCOUNT_FILE = os.getenv("GSHEET_CREDENTIALS_FILE", "gspread_key.json")


# FIX (BUG 4): All authentication logic moved inside the function.
# Previously this ran at import time, crashing the entire app if credentials
# were missing — even when only running the scraper without Google Sheets.
def export_to_gsheet():

    # --------------------
    # AUTH (lazy — only runs when this function is called)
    # --------------------
    gsheet_credentials_json = os.getenv("GSHEET_CREDENTIALS_JSON")

    if gsheet_credentials_json:
        with open(SERVICE_ACCOUNT_FILE, "w") as f:
            f.write(gsheet_credentials_json)

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Google Sheets credentials not found. "
            f"Provide either GSHEET_CREDENTIALS_JSON env var or a local file at {SERVICE_ACCOUNT_FILE}"
        )

    with open(SERVICE_ACCOUNT_FILE, "r") as f:
        creds_dict = json.load(f)

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

    # --------------------
    # FIX (BUG 5): worksheet.row_count returns the total allocated grid rows
    # (default 1000), not rows with data. Use get_all_values() instead to
    # correctly detect whether the sheet is empty and get existing IDs.
    # --------------------
    all_values = worksheet.get_all_values()

    if len(all_values) == 0:
        # Sheet is completely empty — header not yet written
        existing_ids = set()
        needs_header = True
    else:
        # First row is the header; collect IDs from column 1 (index 0)
        existing_ids = set(
            row[0] for row in all_values[1:] if row and row[0]
        )
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

    # --------------------
    # FILTER NEW ROWS
    # --------------------
    df["id"] = df["id"].astype(str)
    new_rows = df[~df["id"].isin(existing_ids)]

    if new_rows.empty:
        print("No new rows to append.")
        return

    # --------------------
    # WRITE HEADER (only if sheet was empty)
    # --------------------
    if needs_header:
        worksheet.append_row(list(new_rows.columns))

    # --------------------
    # APPEND DATA
    # --------------------
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
