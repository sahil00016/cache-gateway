"""Tests for the response envelope models."""

from src.core.envelope import (
    ErrorBody,
    ErrorResponse,
    PageMeta,
    PaginatedResponse,
    SuccessResponse,
)


def test_success_response_is_always_flagged_successful() -> None:
    assert SuccessResponse[int](data=1).success is True


def test_error_response_is_always_flagged_unsuccessful() -> None:
    response = ErrorResponse(error=ErrorBody(code="not_found", message="gone"))

    assert response.success is False


def test_error_response_carries_no_data_field() -> None:
    response = ErrorResponse(error=ErrorBody(code="x", message="y"))

    assert "data" not in response.model_dump()


def test_paginated_response_carries_page_metadata() -> None:
    response = PaginatedResponse[str](data=["a", "b"], meta=PageMeta(total=2, limit=10, offset=0))

    assert response.meta.total == 2
    assert response.data == ["a", "b"]
