"""Tests for the product service.

The service is exercised against a fake repository rather than a database.
That keeps these fast and, more usefully, makes the *number of repository
calls* assertable -- which is exactly what the caching work at M3 to M7 will
need to prove.
"""

from datetime import UTC, datetime

import pytest

from src.core.exceptions import NotFoundError
from src.model.product import Product
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


async def test_get_returns_the_product() -> None:
    service = ProductService(FakeRepository({7}))  # type: ignore[arg-type]

    product = await service.get(7)

    assert product.id == 7
    assert product.sku == "SKU-000000007"


async def test_get_raises_not_found_for_an_absent_id() -> None:
    service = ProductService(FakeRepository(set()))  # type: ignore[arg-type]

    with pytest.raises(NotFoundError) as exc:
        await service.get(404)

    assert exc.value.details == {"product_id": 404}


async def test_get_hits_the_database_exactly_once() -> None:
    # The baseline contract: one read, one query. Every protection added at
    # M3 to M7 is measured as a reduction against this.
    repository = FakeRepository({1})
    service = ProductService(repository)  # type: ignore[arg-type]

    await service.get(1)

    assert repository.get_by_id_calls == [1]


async def test_repeated_reads_of_the_same_id_each_hit_the_database() -> None:
    # Documents the absence of caching. When M3 lands, this test is expected
    # to change -- and that change is the point of the milestone.
    repository = FakeRepository({1})
    service = ProductService(repository)  # type: ignore[arg-type]

    for _ in range(5):
        await service.get(1)

    assert repository.get_by_id_calls == [1, 1, 1, 1, 1]


async def test_get_many_issues_one_query_not_n() -> None:
    # An N+1 here would make the batch endpoint useless for demonstrating
    # stampede behaviour later.
    repository = FakeRepository({1, 2, 3})
    service = ProductService(repository)  # type: ignore[arg-type]

    await service.get_many([1, 2, 3])

    assert repository.get_many_calls == [[1, 2, 3]]


async def test_get_many_omits_missing_ids_rather_than_raising() -> None:
    service = ProductService(FakeRepository({1, 3}))  # type: ignore[arg-type]

    products = await service.get_many([1, 2, 3])

    assert [p.id for p in products] == [1, 3]


async def test_get_many_with_no_ids_does_not_touch_the_database() -> None:
    repository = FakeRepository(set())
    service = ProductService(repository)  # type: ignore[arg-type]

    assert await service.get_many([]) == []
    assert repository.get_many_calls == [[]]
