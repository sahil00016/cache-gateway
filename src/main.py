"""Application factory.

This module is deliberately free of side effects: importing it must not build
an app or read configuration. The ASGI entrypoint servers bind to lives in
``src.asgi``.

That separation is not cosmetic. With ``app = create_app()`` here, any import
of this module validated settings, so a tool that only wanted the OpenAPI
schema still needed a database URL -- which passed locally, where a .env file
exists, and failed in CI, where none does.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.router import api_router
from src.common import openapi
from src.common.settings import Settings, get_settings
from src.core.handlers import register_exception_handlers
from src.core.lifecycle import lifespan
from src.core.middleware import AccessLogMiddleware, RequestIdMiddleware
from src.core.tracing import configure_tracing


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the ASGI application.

    A factory rather than a module-level singleton so tests can build an app
    with overridden settings without mutating global state.

    Args:
        settings: Overrides the process settings. Used by tests.

    Returns:
        The configured application.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=openapi.TITLE,
        description=openapi.DESCRIPTION,
        version="0.1.0",
        contact=openapi.CONTACT,
        openapi_tags=openapi.TAGS_METADATA,
        docs_url=settings.docs_url,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Middleware is LIFO: the last added runs outermost. The request id must be
    # set before the access log reads it, so it is added last.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    # CORS is installed only when an origin is actually configured. The default
    # is no middleware rather than a permissive one: this API has no browser
    # client until the M9 dashboard exists, and a service that allows any
    # origin by default is one deployment away from allowing it in production.
    # Origins are listed explicitly -- `allow_origins=["*"]` with
    # `allow_credentials=True` is rejected by browsers anyway, and silently
    # degrades rather than failing loudly.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "PUT", "DELETE", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-Request-ID"],
        )

    register_exception_handlers(app)
    configure_tracing(app, settings)
    app.include_router(api_router)

    return app
