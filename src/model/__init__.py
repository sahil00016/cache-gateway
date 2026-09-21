"""SQLAlchemy table definitions. No business logic.

Every model is imported here so that ``Base.metadata`` is complete by the time
Alembic inspects it. A model that is not imported is invisible to autogenerate,
which silently produces an empty migration.
"""

from src.model.base import Base, TimestampMixin
from src.model.product import Product

__all__ = ["Base", "Product", "TimestampMixin"]
