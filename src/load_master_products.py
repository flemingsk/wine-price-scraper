import csv
from sqlalchemy.orm import Session

from src.db import SessionLocal
from src.models import MasterProduct

CSV_PATH = "master_products.csv"


def clean(value):
    if value is None:
        return None
    value = value.strip()
    return value if value != "" else None


def main():
    session: Session = SessionLocal()

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        print("CSV headers detected:", reader.fieldnames)

        for raw_row in reader:
            row = {k.strip(): clean(v) for k, v in raw_row.items() if k}

            # Mandatory fields
            if not row.get("estate_name") or not row.get("retailer"):
                continue

            product_url = row.get("product_url")
            url_template = row.get("url_template")

            # Invariant enforcement
            if row["retailer"].lower() == "millesima":
                if not url_template:
                    raise ValueError(
                        f"Millesima row missing url_template: {row}"
                    )
                product_url = None
            else:
                if not product_url:
                    raise ValueError(
                        f"Non-millesima row missing product_url: {row}"
                    )
                url_template = None

            # Idempotent uniqueness check
            query = session.query(MasterProduct).filter(
                MasterProduct.retailer == row["retailer"]
            )

            if product_url:
                query = query.filter(
                    MasterProduct.product_url == product_url
                )
            else:
                query = query.filter(
                    MasterProduct.product_url.is_(None),
                    MasterProduct.url_template == url_template,
                )

            if query.first():
                continue

            product = MasterProduct(
                estate_name=row["estate_name"],
                retailer=row["retailer"],
                product_url=product_url,
                url_template=url_template,
                price_selector=row["price_selector"],
                vintage_start=int(row["vintage_start"])
                if row.get("vintage_start")
                else None,
                vintage_end=int(row["vintage_end"])
                if row.get("vintage_end")
                else None,
                bottle_size=row.get("bottle_size"),
                active=str(row.get("active", "true")).lower()
                in ("true", "1", "yes"),
                notes=row.get("notes"),
            )

            session.add(product)

        session.commit()

    session.close()
    print("Master products loaded successfully.")


if __name__ == "__main__":
    main()
