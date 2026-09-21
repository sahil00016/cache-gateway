"""HTTP middleware: correlation ids and access logging."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.context import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign a correlation id to every request and echo it back.

    An inbound ``X-Request-ID`` is honoured so a caller can correlate across
    service boundaries; otherwise one is generated.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Set the request id for the duration of the request.

        Args:
            request: The inbound request.
            call_next: The next handler in the chain.

        Returns:
            The response, with the correlation id header attached.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one structured log line per request, with its duration."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Time the request and log the outcome.

        Args:
            request: The inbound request.
            call_next: The next handler in the chain.

        Returns:
            The unmodified response.
        """
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000

        logger.info(
            "request",
            extra={
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response
