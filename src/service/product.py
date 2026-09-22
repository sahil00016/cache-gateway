"""Product business logic.

M3 adds cache-aside reads for :meth:`get`: check Redis, and on a miss fall
through to Postgres and populate the cache on the way out. Four failure modes
of that scheme are deliberately not addressed here and are the subject of
M4-M7 -- see the project design document. See ADR-0009 for the measured cost
of adding this layer: it reduces database load, but tail latency got worse,
not better.

:meth:`get_many` stays uncached. Caching it now would blur two different
things this project measures separately: the ordinary hit-rate win here, and
the thundering-herd behaviour a batch of cold keys produces, which is M6's
subject and needs an uncached baseline of its own.
"""

from src.core.exceptions import NotFoundError
from src.repository.product import ProductRepository
from src.repository.product_cache import ProductCacheRepository
from src.schema.product import ProductRead


class ProductService:
    """Reads products, cache-aside on the single-item path."""

    def __init__(self, repository: ProductRepository, cache: ProductCacheRepository) -> None:
        """Bind the service to a repository and its cache.

        Args:
            repository: Data access for the current request.
            cache: Cache-aside reads for the current request.
        """
        self._repository = repository
        self._cache = cache

    async def get(self, product_id: int) -> ProductRead:
        """Return a single product, checking the cache before Postgres.

        Args:
            product_id: The product's id.

        Returns:
            The product.

        Raises:
            NotFoundError: If no live product has that id.
        """
        cached = await self._cache.get(product_id)
        if cached is not None:
            return cached

        product = await self._repository.get_by_id(product_id)
        if product is None:
            raise NotFoundError(
                f"No product with id {product_id}.",
                details={"product_id": product_id},
            )

        result = ProductRead.model_validate(product)
        await self._cache.set(product_id, result)
        return result

    async def get_many(self, product_ids: list[int]) -> list[ProductRead]:
        """Return several products, skipping any that do not exist.

        Missing ids are omitted rather than raising, so one absent product does
        not fail the whole batch. Always reads through to Postgres -- see the
        module docstring for why this path is not cached yet.

        Args:
            product_ids: Ids to fetch.

        Returns:
            The products that were found.
        """
        products = await self._repository.get_many_by_id(product_ids)
        return [ProductRead.model_validate(product) for product in products]
