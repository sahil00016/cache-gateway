"""Product read endpoints.

The routes are the stable surface of this service. Their behaviour does not
change as the cache and its protections are added underneath at M3 to M7 --
only their latency and the database load behind them does. That is what makes
before-and-after benchmarking meaningful.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from src.api.deps import ProductServiceDep
from src.core.envelope import PageMeta, PaginatedResponse, SuccessResponse
from src.schema.product import ProductRead, ProductUpdate

router = APIRouter(prefix="/v1/products", tags=["products"])

ProductId = Annotated[int, Path(ge=1, description="Product id.")]

_MAX_BATCH = 100


@router.get(
    "",
    response_model=PaginatedResponse[ProductRead],
    summary="Fetch several products by id",
)
async def get_products(
    service: ProductServiceDep,
    ids: Annotated[
        list[int],
        Query(
            min_length=1,
            max_length=_MAX_BATCH,
            description="Product ids. Repeat the parameter: ?ids=1&ids=2",
        ),
    ],
) -> PaginatedResponse[ProductRead]:
    """Return several products in one request.

    Exists mainly to expose stampede behaviour more sharply than the single
    read can: one request can miss on many keys at once.

    Args:
        service: Injected product service.
        ids: Product ids to fetch, capped to keep one request from becoming a
            denial-of-service vector against the database.

    Returns:
        The products that were found. Missing ids are omitted, not errors.
    """
    products = await service.get_many(ids)
    return PaginatedResponse[ProductRead](
        data=products,
        meta=PageMeta(total=len(products), limit=len(ids), offset=0),
    )


@router.get(
    "/{product_id}",
    response_model=SuccessResponse[ProductRead],
    summary="Fetch one product by id",
)
async def get_product(
    product_id: ProductId,
    service: ProductServiceDep,
) -> SuccessResponse[ProductRead]:
    """Return a single product.

    Args:
        product_id: The product's id.
        service: Injected product service.

    Returns:
        The product, wrapped in the standard success envelope.
    """
    product = await service.get(product_id)
    return SuccessResponse[ProductRead](data=product)


@router.put(
    "/{product_id}",
    response_model=SuccessResponse[ProductRead],
    summary="Update a product",
)
async def update_product(
    product_id: ProductId,
    update_data: ProductUpdate,
    service: ProductServiceDep,
) -> SuccessResponse[ProductRead]:
    """Update a product and invalidate its cache entry.

    M7: Cache-aside with delete-on-write. After updating Postgres, the cache
    entry is deleted so the next read fetches the new value. See ADR-0006.

    Args:
        product_id: The product's id.
        update_data: Fields to update (any omitted fields remain unchanged).
        service: Injected product service.

    Returns:
        The updated product, wrapped in the standard success envelope.
    """
    product = await service.update(product_id, update_data)
    return SuccessResponse[ProductRead](data=product)


@router.delete(
    "/{product_id}",
    status_code=204,
    summary="Delete a product",
)
async def delete_product(
    product_id: ProductId,
    service: ProductServiceDep,
) -> None:
    """Soft delete a product and invalidate its cache entry.

    M7: Cache-aside with delete-on-write. The Bloom filter cannot remove keys,
    so a deleted product becomes a false positive until rebuild. This trade-off
    is measured in the benchmarks. See ADR-0003 and ADR-0006.

    Args:
        product_id: The product's id.
        service: Injected product service.
    """
    await service.delete(product_id)
