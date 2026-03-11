from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    CheckConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class MasterProduct(Base):
    __tablename__ = "master_products"

    # FIX (BUG 2): __table_args__ must be inside the class, not at module level
    __table_args__ = (
        CheckConstraint(
            "product_url IS NOT NULL OR url_template IS NOT NULL",
            name="product_url_or_template_required",
        ),
    )

    id = Column(Integer, primary_key=True)

    # Identity
    estate_name = Column(String, nullable=False)
    retailer = Column(String, nullable=False)

    # URLs
    product_url = Column(Text, nullable=True)
    url_template = Column(Text, nullable=True)

    # Scraping
    price_selector = Column(Text, nullable=False)
    availability_selector = Column(Text, nullable=True)

    # FIX (BUG 3): vintage_start / vintage_end must be nullable=True to match
    # actual usage — non-vintage wines have no range and the loader inserts None
    vintage_start = Column(Integer, nullable=True)
    vintage_end = Column(Integer, nullable=True)

    # Wine metadata
    wine_color = Column(String, nullable=False, default="Rouge")

    # Metadata
    bottle_size = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

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

    vintage = Column(Integer, nullable=True)
    wine_color = Column(String, nullable=False, default="Rouge")

    price_amount = Column(Numeric(10, 2))
    currency = Column(String)
    raw_price_text = Column(Text)
    availability = Column(Boolean)

    fetched_at = Column(DateTime(timezone=True), nullable=False)

    product = relationship("MasterProduct", back_populates="prices")
