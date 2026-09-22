"""Product business logic.

At M2 this layer is intentionally thin -- it reads straight through to
Postgres with no cache at all. That is the point: it is the control against
which every later protection is measured.

Milestones M3 to M7 add the cache and its four failure-mode fixes *here*,
leaving the API and repository layers untouched.
"""

from src.core.exceptions import NotFoundError
from src.repository.product import ProductRepository
from src.schema.product import ProductRead


class ProductService:
    """Reads products. No caching yet -- see the module docstring."""

    def __init__(self, repository: ProductRepository) -> None:
        """Bind the service to a repository.

        Args:
            repository: Data access for the current request.
        """
        self._repository = repository

    async def get(self, product_id: int) -> ProductRead:
        """Return a single product.

        Args:
            product_id: The product's id.

        Returns:
            The product.

        Raises:
            NotFoundError: If no live product has that id.
        """
        product = await self._repository.get_by_id(product_id)
        if product is None:
            raise NotFoundError(
                f"No product with id {product_id}.",
                details={"product_id": product_id},
            )
        return ProductRead.model_validate(product)

    async def get_many(self, product_ids: list[int]) -> list[ProductRead]:
        """Return several products, skipping any that do not exist.

        Missing ids are omitted rather than raising, so one absent product does
        not fail the whole batch.

        Args:
            product_ids: Ids to fetch.

        Returns:
            The products that were found.
        """
        products = await self._repository.get_many_by_id(product_ids)
        return [ProductRead.model_validate(product) for product in products]
