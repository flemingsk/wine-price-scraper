from dotenv import load_dotenv; load_dotenv()
import pandas as pd
from sqlalchemy import text
from src.db import engine

with engine.connect() as conn:
    df = pd.read_sql(text("""
        SELECT mp.estate_name, mp.retailer, mp.wine_color,
               mp.vintage_start,
               COUNT(*) AS null_vintage_records,
               MIN(DATE(pr.fetched_at AT TIME ZONE 'UTC')) AS first_seen,
               MAX(DATE(pr.fetched_at AT TIME ZONE 'UTC')) AS last_seen
        FROM price_records pr
        JOIN master_products mp ON mp.id = pr.master_product_id
        WHERE pr.vintage IS NULL
        GROUP BY mp.estate_name, mp.retailer, mp.wine_color, mp.vintage_start
        ORDER BY mp.estate_name, mp.retailer
    """), conn)

    total = int(df["null_vintage_records"].sum()) if not df.empty else 0
    print(f"NULL-vintage price_records: {total}")
    if not df.empty:
        print(df.to_string(index=False))
    else:
        print("None found.")
