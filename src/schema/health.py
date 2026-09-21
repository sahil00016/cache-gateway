"""Schemas for the health and readiness endpoints."""

from pydantic import BaseModel, Field


class LivenessResult(BaseModel):
    """Result of a liveness probe."""

    status: str = Field(description="Always 'ok' when the process is running.")
    service: str = Field(description="Service name, for probe-side assertions.")


class DependencyStatus(BaseModel):
    """Reachability of a single dependency."""

    name: str = Field(description="Dependency identifier, e.g. 'postgres'.")
    healthy: bool = Field(description="Whether the dependency answered.")
    latency_ms: float | None = Field(
        default=None, description="Round-trip time of the probe query."
    )
    error: str | None = Field(default=None, description="Failure reason, if unhealthy.")


class ReadinessResult(BaseModel):
    """Result of a readiness probe across every dependency."""

    ready: bool = Field(description="True only when every dependency is healthy.")
    dependencies: list[DependencyStatus]
