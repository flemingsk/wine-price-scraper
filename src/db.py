import logging
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .models import Base

logger = logging.getLogger(__name__)

# Always read DATABASE_URL from environment; no fallback hardcoded credentials
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Columns added after the initial table creation.
# ADD COLUMN IF NOT EXISTS is idempotent — safe to run on every boot.
_MIGRATIONS = [
    "ALTER TABLE price_records ADD COLUMN IF NOT EXISTS price_corrected  BOOLEAN     NOT NULL DEFAULT FALSE",
    "ALTER TABLE price_records ADD COLUMN IF NOT EXISTS original_price   NUMERIC(10,2)",
    "ALTER TABLE price_records ADD COLUMN IF NOT EXISTS correction_reason TEXT",
    "ALTER TABLE master_products ALTER COLUMN price_selector        DROP NOT NULL",
    "ALTER TABLE master_products ALTER COLUMN availability_selector DROP NOT NULL",
]


def init_db():
    """Create tables if they don't exist, then apply additive column migrations."""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for sql in _MIGRATIONS:
            try:
                conn.execute(text(sql))
            except Exception as exc:
                logger.warning("Migration skipped (%s): %s", sql.split()[0:5], exc)
