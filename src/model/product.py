"""The products table.

A deliberately ordinary read-heavy table. It exists to be queried a lot, not to
be interesting: the subject of this service is the cache in front of it.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.model.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    """A product row."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Soft delete.

    Deliberately soft rather than hard. A hard delete would make the Bloom
    filter's inability to remove a key untestable, and that limitation is the
    most interesting thing about failure mode 1.
    """

    __table_args__ = (
        # Reads filter on `deleted_at IS NULL`; a partial index keeps it small
        # and means the planner never scans tombstoned rows.
        Index(
            "ix_products_category_live",
            "category",
            postgresql_where=deleted_at.is_(None),
        ),
    )
