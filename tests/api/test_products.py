"""Tests for the product endpoints.

The service is replaced via FastAPI's dependency overrides, so these assert the
HTTP contract -- status codes, envelope shape, validation -- without a database.
"""

from datetime import UTC, datetime

import httpx
import pytest

from src.api.deps import get_product_service
from src.core.exceptions import NotFoundError
from src.main import create_app
from src.schema.product import ProductRead


def _read(product_id: int) -> ProductRead:
    now = datetime.now(UTC)
    return ProductRead(
        id=product_id,
        sku=f"SKU-{product_id:09d}",
        name=f"product {product_id}",
        description=None,
        price_cents=1999,
        category="books",
        created_at=now,
        updated_at=now,
    )


class StubService:
    def __init__(self, existing: set[int]) -> None:
        self.existing = existing

    async def get(self, product_id: int) -> ProductRead:
        if product_id not in self.existing:
            raise NotFoundError(
                f"No product with id {product_id}.", details={"product_id": product_id}
            )
        return _read(product_id)

    async def get_many(self, product_ids: list[int]) -> list[ProductRead]:
        return [_read(i) for i in product_ids if i in self.existing]


@pytest.fixture
async def products_client(settings) -> httpx.AsyncClient:
    app = create_app(settings)
    app.dependency_overrides[get_product_service] = lambda: StubService({1, 2, 3})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_get_product_returns_the_success_envelope(
    products_client: httpx.AsyncClient,
) -> None:
    response = await products_client.get("/v1/products/1")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["id"] == 1
    assert body["data"]["sku"] == "SKU-000000001"


async def test_absent_product_returns_404_in_the_error_envelope(
    products_client: httpx.AsyncClient,
) -> None:
    response = await products_client.get("/v1/products/999")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "not_found_error"
    assert body["error"]["details"] == {"product_id": 999}
    assert "data" not in body


@pytest.mark.parametrize("bad_id", ["0", "-1", "abc"])
async def test_invalid_ids_are_rejected_before_reaching_the_service(
    products_client: httpx.AsyncClient, bad_id: str
) -> None:
    response = await products_client.get(f"/v1/products/{bad_id}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"


async def test_batch_read_returns_a_paginated_envelope(
    products_client: httpx.AsyncClient,
) -> None:
    response = await products_client.get("/v1/products", params=[("ids", 1), ("ids", 2)])

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["data"]] == [1, 2]
    assert body["meta"]["total"] == 2


async def test_batch_read_omits_missing_ids(products_client: httpx.AsyncClient) -> None:
    response = await products_client.get("/v1/products", params=[("ids", 1), ("ids", 99)])

    assert [item["id"] for item in response.json()["data"]] == [1]


async def test_batch_read_requires_at_least_one_id(
    products_client: httpx.AsyncClient,
) -> None:
    response = await products_client.get("/v1/products")

    assert response.status_code == 422


async def test_batch_read_is_capped(products_client: httpx.AsyncClient) -> None:
    # Without a cap one request could ask for a million ids, turning the batch
    # endpoint into a denial-of-service vector against Postgres.
    response = await products_client.get("/v1/products", params=[("ids", i) for i in range(1, 102)])

    assert response.status_code == 422
