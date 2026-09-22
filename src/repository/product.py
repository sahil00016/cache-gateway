"""Data access for products.

Every method here issues real SQL, and every call increments
``database_queries_total``. That counter is the headline metric of this whole
project -- each protection added later exists to make it smaller -- so it is
incremented at the only layer that actually talks to Postgres, never estimated
further up the stack.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.metrics import database_queries_total
from src.model.product import Product


class ProductRepository:
    """Async data access for the products table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a request-scoped session.

        Args:
            session: The session for the current request. Never shared across
                tasks -- an AsyncSession is not concurrency-safe.
        """
        self._session = session

    async def get_by_id(self, product_id: int) -> Product | None:
        """Fetch a single live product by primary key.

        Args:
            product_id: The product's id.

        Returns:
            The product, or None if it does not exist or is soft-deleted.
        """
        database_queries_total.labels(operation="get_by_id").inc()

        statement = select(Product).where(
            Product.id == product_id,
            Product.deleted_at.is_(None),
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_many_by_id(self, product_ids: list[int]) -> list[Product]:
        """Fetch several live products in one round trip.

        One query regardless of how many ids are requested. Looping over
        :meth:`get_by_id` instead would be an N+1, and would make the batch
        endpoint useless for demonstrating stampede behaviour.

        Args:
            product_ids: Ids to fetch. Order of the result is not guaranteed.

        Returns:
            The products that exist and are not soft-deleted.
        """
        if not product_ids:
            return []

        database_queries_total.labels(operation="get_many_by_id").inc()

        statement = select(Product).where(
            Product.id.in_(product_ids),
            Product.deleted_at.is_(None),
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())
