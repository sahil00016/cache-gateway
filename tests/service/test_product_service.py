"""Tests for the product service.

The service is exercised against fakes for both the repository and the cache,
never a real database or Redis. That keeps these fast and, more usefully,
makes the *number of calls to each* assertable -- which is exactly what the
caching work at M3 to M7 needs to prove: not just that a response is correct,
but which dependency answered it.
"""

from datetime import UTC, datetime

import pytest

from src.core.exceptions import NotFoundError
from src.model.product import Product
from src.schema.product import ProductRead
from src.service.product import ProductService


def _product(product_id: int) -> Product:
    now = datetime.now(UTC)
    return Product(
        id=product_id,
        sku=f"SKU-{product_id:09d}",
        name=f"product {product_id}",
        description=None,
        price_cents=1999,
        category="books",
        created_at=now,
        updated_at=now,
    )


class FakeRepository:
    """Records every call so query counts can be asserted."""

    def __init__(self, existing: set[int]) -> None:
        self.existing = existing
        self.get_by_id_calls: list[int] = []
        self.get_many_calls: list[list[int]] = []

    async def get_by_id(self, product_id: int) -> Product | None:
        self.get_by_id_calls.append(product_id)
        return _product(product_id) if product_id in self.existing else None

    async def get_many_by_id(self, product_ids: list[int]) -> list[Product]:
        self.get_many_calls.append(list(product_ids))
        return [_product(i) for i in product_ids if i in self.existing]


class FakeCache:
    """An in-memory stand-in for :class:`ProductCacheRepository`."""

    def __init__(self) -> None:
        self._store: dict[int, ProductRead] = {}
        self.get_calls: list[int] = []
        self.set_calls: list[int] = []

    async def get(self, product_id: int) -> ProductRead | None:
        self.get_calls.append(product_id)
        return self._store.get(product_id)

    async def set(self, product_id: int, product: ProductRead) -> None:
        self.set_calls.append(product_id)
        self._store[product_id] = product


async def test_get_returns_the_product() -> None:
    service = ProductService(FakeRepository({7}), FakeCache())  # type: ignore[arg-type]

    product = await service.get(7)

    assert product.id == 7
    assert product.sku == "SKU-000000007"


async def test_get_raises_not_found_for_an_absent_id() -> None:
    service = ProductService(FakeRepository(set()), FakeCache())  # type: ignore[arg-type]

    with pytest.raises(NotFoundError) as exc:
        await service.get(404)

    assert exc.value.details == {"product_id": 404}


async def test_get_hits_the_database_exactly_once_on_a_cache_miss() -> None:
    # The baseline contract from M2: a cold key still costs exactly one query.
    repository = FakeRepository({1})
    service = ProductService(repository, FakeCache())  # type: ignore[arg-type]

    await service.get(1)

    assert repository.get_by_id_calls == [1]


async def test_get_populates_the_cache_on_a_miss() -> None:
    repository = FakeRepository({1})
    cache = FakeCache()
    service = ProductService(repository, cache)  # type: ignore[arg-type]

    await service.get(1)

    assert cache.set_calls == [1]


async def test_repeated_reads_of_the_same_id_hit_the_database_once() -> None:
    # This is the M3 contract the M2 version of this test predicted would
    # change: the first read is a miss and costs a query; every read after
    # that is served from the cache the first read populated.
    repository = FakeRepository({1})
    service = ProductService(repository, FakeCache())  # type: ignore[arg-type]

    for _ in range(5):
        await service.get(1)

    assert repository.get_by_id_calls == [1]


async def test_a_cache_hit_never_reaches_the_database() -> None:
    repository = FakeRepository({1})
    cache = FakeCache()
    cache._store[1] = ProductRead(
        id=1,
        sku="SKU-000000001",
        name="preloaded",
        description=None,
        price_cents=1,
        category="books",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    service = ProductService(repository, cache)  # type: ignore[arg-type]

    product = await service.get(1)

    assert product.name == "preloaded"
    assert repository.get_by_id_calls == []


async def test_a_not_found_result_is_not_cached() -> None:
    # There is no negative cache yet -- a repeated miss keeps costing a query.
    # That gap is a deliberate, documented failure mode for a later milestone,
    # not an oversight here.
    repository = FakeRepository(set())
    cache = FakeCache()
    service = ProductService(repository, cache)  # type: ignore[arg-type]

    for _ in range(3):
        with pytest.raises(NotFoundError):
            await service.get(404)

    assert repository.get_by_id_calls == [404, 404, 404]
    assert cache.set_calls == []


async def test_get_many_issues_one_query_not_n() -> None:
    # An N+1 here would make the batch endpoint useless for demonstrating
    # stampede behaviour later.
    repository = FakeRepository({1, 2, 3})
    service = ProductService(repository, FakeCache())  # type: ignore[arg-type]

    await service.get_many([1, 2, 3])

    assert repository.get_many_calls == [[1, 2, 3]]


async def test_get_many_never_touches_the_cache() -> None:
    # Documented in the service module docstring: caching the batch path is
    # deferred to M6, which needs an uncached baseline to measure against.
    repository = FakeRepository({1, 2, 3})
    cache = FakeCache()
    service = ProductService(repository, cache)  # type: ignore[arg-type]

    await service.get_many([1, 2, 3])

    assert cache.get_calls == []
    assert cache.set_calls == []


async def test_get_many_omits_missing_ids_rather_than_raising() -> None:
    service = ProductService(FakeRepository({1, 3}), FakeCache())  # type: ignore[arg-type]

    products = await service.get_many([1, 2, 3])

    assert [p.id for p in products] == [1, 3]


async def test_get_many_with_no_ids_does_not_touch_the_database() -> None:
    repository = FakeRepository(set())
    service = ProductService(repository, FakeCache())  # type: ignore[arg-type]

    assert await service.get_many([]) == []
    assert repository.get_many_calls == [[]]
