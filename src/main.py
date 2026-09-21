"""Application factory and ASGI entrypoint."""

from fastapi import FastAPI

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

    register_exception_handlers(app)
    configure_tracing(app, settings)
    app.include_router(api_router)

    return app


app = create_app()
