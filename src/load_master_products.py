import csv
from sqlalchemy.orm import Session
from src import SessionLocal
from src import MasterProduct

CSV_PATH = "master_products.csv"


def clean(value):
    if value is None:
        return None
    return value.strip()


def main():
    session: Session = SessionLocal()

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        print("CSV headers detected:", reader.fieldnames)

        for raw_row in reader:
            row = {k.strip(): clean(v) for k, v in raw_row.items() if k}

            if not row.get("estate_name") or not row.get("retailer"):
                continue

            existing = (
                session.query(MasterProduct)
                .filter(
                    MasterProduct.estate_name == row["estate_name"],
                    MasterProduct.retailer == row["retailer"],
                )
                .first()
            )

            if existing:
                continue

            product = MasterProduct(
                estate_name=row["estate_name"],
                retailer=row["retailer"],
                product_url=row["product_url"],
                url_template=row.get("url_template") or None,
                price_selector=row["price_selector"],
                vintage_start=int(row["vintage_start"]),
                vintage_end=int(row["vintage_end"]),
                bottle_size=row["bottle_size"],
                active=str(row["active"]).lower() in ("true", "1", "yes"),
                notes=row.get("notes"),
            )

            session.add(product)

        session.commit()

    session.close()
    print("Master products loaded successfully.")


if __name__ == "__main__":
    main()