import psycopg2
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from src.db import engine

# --------------------
# CONFIG
# --------------------
DATABASE_URL = "postgresql://postgres:StrongPassword123@localhost:5432/wine_prices"
GOOGLE_SHEET_NAME = "Wine Prices"
TAB_NAME = "price_records"
SERVICE_ACCOUNT_FILE = "gspread_key.json"


def export_to_gsheet():
    # --------------------
    # GOOGLE SHEETS AUTH
    # --------------------
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        SERVICE_ACCOUNT_FILE, scope
    )
    client = gspread.authorize(creds)

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
    conn = psycopg2.connect(DATABASE_URL)

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
    conn.close()

    # --------------------
    # FILTER NEW ROWS
    # --------------------
    df["id"] = df["id"].astype(str)
    new_rows = df[~df["id"].isin(existing_ids)]

    if new_rows.empty:
        print("No new rows to append.")
        return

    # --------------------
    # WRITE HEADER (once)
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


# Allow manual execution if needed
if __name__ == "__main__":
    export_to_gsheet()
