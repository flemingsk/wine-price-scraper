from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class MasterProduct(Base):
    __tablename__ = "master_products"

    id = Column(Integer, primary_key=True)

    # Identity
    estate_name = Column(String, nullable=False)
    retailer = Column(String, nullable=False)

    # URLs
    product_url = Column(Text, nullable=False)
    url_template = Column(Text, nullable=True)

    # Scraping
    price_selector = Column(Text, nullable=False)
    availability_selector = Column(Text, nullable=True)

    # Vintage range (NO single vintage here anymore)
    vintage_start = Column(Integer, nullable=False)
    vintage_end = Column(Integer, nullable=False)

    # Metadata
    bottle_size = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

    # Relationships
    prices = relationship(
        "PriceRecord",
        back_populates="product",
        cascade="all, delete-orphan",
    )


class PriceRecord(Base):
    __tablename__ = "price_records"

    id = Column(Integer, primary_key=True)
    master_product_id = Column(
        Integer,
        ForeignKey("master_products.id", ondelete="CASCADE"),
        nullable=False,
    )

    site = Column(String, nullable=False)
    url = Column(Text, nullable=False)

    price_amount = Column(Numeric(10, 2))
    currency = Column(String)
    raw_price_text = Column(Text)
    availability = Column(Boolean)

    note = Column(Text, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)

    product = relationship("MasterProduct", back_populates="prices")
