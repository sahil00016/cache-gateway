"""Uniform response envelopes.

Handlers never return a bare dict. Every response, success or failure, is
wrapped in one of the models below, so a client can parse any response from
this service with a single schema. Enforced by a test over the OpenAPI output.
"""

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """The error payload carried by a failed response."""

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable description.")
    details: dict[str, object] = Field(
        default_factory=dict,
        description="Structured context. Never contains sensitive values.",
    )


class SuccessResponse[T](BaseModel):
    """A successful response carrying a single payload."""

    success: bool = Field(default=True, description="Always true.")
    data: T


class ErrorResponse(BaseModel):
    """A failed response. Never carries a data field."""

    success: bool = Field(default=False, description="Always false.")
    error: ErrorBody


class PageMeta(BaseModel):
    """Pagination metadata for a list response."""

    total: int = Field(ge=0, description="Total items matching the query.")
    limit: int = Field(ge=1, description="Page size requested.")
    offset: int = Field(ge=0, description="Items skipped before this page.")


class PaginatedResponse[T](BaseModel):
    """A successful response carrying a page of items."""

    success: bool = Field(default=True, description="Always true.")
    data: list[T]
    meta: PageMeta
