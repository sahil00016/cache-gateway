"""Tests for the typed exception hierarchy."""

from http import HTTPStatus

import pytest

from src.core.exceptions import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationError,
)


@pytest.mark.parametrize(
    ("error_class", "expected_code"),
    [
        (NotFoundError, "not_found_error"),
        (ValidationError, "validation_error"),
        (ConflictError, "conflict_error"),
        (DependencyUnavailableError, "dependency_unavailable_error"),
    ],
)
def test_code_is_derived_from_the_class_name(
    error_class: type[AppError], expected_code: str
) -> None:
    # Codes are derived, not hand-maintained, so they cannot drift from the
    # class they describe.
    assert error_class.code() == expected_code


def test_default_message_is_used_when_none_supplied() -> None:
    assert NotFoundError().message == NotFoundError.default_message


def test_supplied_message_overrides_the_default() -> None:
    assert NotFoundError("no such product").message == "no such product"


def test_details_default_to_an_empty_dict_not_none() -> None:
    assert NotFoundError().details == {}


def test_status_codes_are_class_level_not_call_site_decisions() -> None:
    assert NotFoundError.status_code == HTTPStatus.NOT_FOUND
    assert ConflictError.status_code == HTTPStatus.CONFLICT
    assert DependencyUnavailableError.status_code == HTTPStatus.SERVICE_UNAVAILABLE
