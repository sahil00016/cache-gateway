"""Typed exception hierarchy for the service.

Every error the service raises derives from :class:`AppError`. The HTTP status
and the machine-readable error code are properties of the exception class, not
decisions made at the call site, so a given failure is reported identically no
matter which handler happens to surface it.

The ``code`` is derived from the class name automatically. ``ProductNotFound``
becomes ``product_not_found``. This keeps codes and classes from drifting apart,
which they always do when both are maintained by hand.
"""

import re
from http import HTTPStatus
from typing import Any

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


class AppError(Exception):
    """Base class for every error raised deliberately by this service.

    Attributes:
        status_code: HTTP status this error maps to.
        message: Human-readable description, safe to return to a client.
        details: Optional structured context, safe to return to a client.
    """

    status_code: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            message: Overrides ``default_message`` when supplied.
            details: Structured context to include in the response body.
        """
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)

    @classmethod
    def code(cls) -> str:
        """Return the stable machine-readable code for this error class.

        Derived from the class name so the two cannot drift apart.

        Returns:
            The snake_case error code, for example ``product_not_found``.
        """
        return _CAMEL_BOUNDARY.sub("_", cls.__name__).lower()


# --------------------------------------------------------------------------- #
# 4xx -- the caller can fix these
# --------------------------------------------------------------------------- #
class NotFoundError(AppError):
    """The requested resource does not exist."""

    status_code = HTTPStatus.NOT_FOUND
    default_message = "The requested resource was not found."


class ValidationError(AppError):
    """The request was structurally valid but semantically wrong."""

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    default_message = "The request could not be processed."


class ConflictError(AppError):
    """The request conflicts with the current state of the resource."""

    status_code = HTTPStatus.CONFLICT
    default_message = "The request conflicts with the current state."


# --------------------------------------------------------------------------- #
# 5xx -- the caller cannot fix these
# --------------------------------------------------------------------------- #
class DependencyError(AppError):
    """An upstream dependency failed.

    Raised when a dependency is reachable but did not behave. A dependency that
    is entirely unreachable should surface as :class:`DependencyUnavailableError`
    so the two can be told apart in metrics.
    """

    status_code = HTTPStatus.BAD_GATEWAY
    default_message = "An upstream dependency failed."


class DependencyUnavailableError(AppError):
    """An upstream dependency could not be reached at all."""

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    default_message = "An upstream dependency is unavailable."


class ConfigurationError(AppError):
    """The service is misconfigured and cannot serve traffic correctly."""

    status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    default_message = "The service is misconfigured."
