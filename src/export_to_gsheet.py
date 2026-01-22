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


# --------------------
# GOOGLE SHEETS AUTH
# --------------------
GSHEET_CREDENTIALS_JSON = os.getenv("GSHEET_CREDENTIALS_JSON")

if not GSHEET_CREDENTIALS_JSON:
    raise RuntimeError(
        "GSHEET_CREDENTIALS_JSON environment variable is not set"
    )

creds_dict = json.loads(GSHEET_CREDENTIALS_JSON)

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    creds_dict, scope
)

client = gspread.authorize(creds)


def export_to_gsheet():
    # --------------------
    # OPEN SHEET
    # --------------------
    sheet = client.open(GOOGLE_SHEET_NAME)
    worksheet = sheet.worksheet(TAB_NAME)

    # --------------------
    # FETCH EXISTING IDS
    # --------------------
    existing_ids = set()
    if worksheet.row_count > 1:
        existing_ids = set(worksheet.col_values(1)[1:])

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
    # WRITE HEADER (if empty)
    # --------------------
    if worksheet.row_count == 0:
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


# Allow manual execution
if __name__ == "__main__":
    export_to_gsheet()
