"""OpenTelemetry wiring.

Tracing is opt-in via ``OTEL_ENABLED``. When disabled, no exporter is installed
and no spans are created, so the default local run has no collector dependency.
"""

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from src.common.settings import Settings

logger = logging.getLogger(__name__)


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    """Install the OTLP exporter and instrument the application.

    Args:
        app: The FastAPI application to instrument.
        settings: Validated application settings.
    """
    if not settings.otel_enabled:
        logger.info("tracing_disabled")
        return

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment": settings.environment.value,
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)

    # Imported lazily: these packages monkey-patch on import, which should not
    # happen in a process that has tracing switched off.
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    logger.info("tracing_enabled", extra={"endpoint": settings.otel_exporter_otlp_endpoint})
