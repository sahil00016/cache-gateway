"""Central exception handlers.

Every error leaves the service through exactly one of these, so the envelope
shape is guaranteed. Handlers are registered on the app in
:func:`register_exception_handlers`.
"""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.envelope import ErrorBody, ErrorResponse
from src.core.exceptions import AppError

logger = logging.getLogger(__name__)


def _render(status: HTTPStatus | int, body: ErrorBody) -> JSONResponse:
    """Serialise an error body into the standard envelope.

    Args:
        status: HTTP status code for the response.
        body: The error payload.

    Returns:
        A JSON response wrapping the error.
    """
    return JSONResponse(
        status_code=int(status),
        content=ErrorResponse(error=body).model_dump(mode="json"),
    )


async def handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    """Handle an error this service raised deliberately.

    Args:
        _: The request, unused.
        exc: The raised error.

    Returns:
        The rendered error response.
    """
    assert isinstance(exc, AppError)  # noqa: S101 -- guarded by registration
    if exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        logger.error("app_error", extra={"error_code": exc.code()}, exc_info=exc)
    else:
        logger.info("app_error", extra={"error_code": exc.code()})

    return _render(
        exc.status_code,
        ErrorBody(code=exc.code(), message=exc.message, details=dict(exc.details)),
    )


async def handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    """Handle a request that failed schema validation.

    Args:
        _: The request, unused.
        exc: The validation error raised by FastAPI.

    Returns:
        The rendered error response.
    """
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    return _render(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        ErrorBody(
            code="request_validation_failed",
            message="The request payload failed validation.",
            details={"errors": exc.errors()},
        ),
    )


async def handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    """Handle a framework-raised HTTP exception, such as a 404 from routing.

    Args:
        _: The request, unused.
        exc: The framework exception.

    Returns:
        The rendered error response.
    """
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    status = HTTPStatus(exc.status_code)
    return _render(
        status,
        ErrorBody(code=status.name.lower(), message=str(exc.detail)),
    )


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    """Handle anything that escaped every other handler.

    The message is deliberately generic: an unexpected exception may carry
    internal detail, and that must not reach a client. The full traceback goes
    to the log, correlated by request id.

    Args:
        _: The request, unused.
        exc: The unhandled exception.

    Returns:
        A generic 500 response.
    """
    logger.exception("unhandled_error", exc_info=exc)
    return _render(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        ErrorBody(code="internal_error", message="An unexpected error occurred."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler to the application.

    Args:
        app: The FastAPI application being configured.
    """
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)
