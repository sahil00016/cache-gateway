"""Enumerations shared across the service."""

from enum import StrEnum


class Environment(StrEnum):
    """Deployment environment the process is running in."""

    LOCAL = "local"
    TEST = "test"
    UAT = "uat"
    PROD = "prod"

    @property
    def is_production_like(self) -> bool:
        """Whether safety rails intended for real traffic should be enforced.

        Returns:
            True for UAT and PROD, False otherwise.
        """
        return self in (Environment.UAT, Environment.PROD)


class LogLevel(StrEnum):
    """Standard library logging levels, as configuration values."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class WriteStrategy(StrEnum):
    """How a write reconciles the cache with the database.

    Both strategies are implemented so their race windows can be measured
    against each other; see failure mode 4 in the project design document.
    """

    CACHE_ASIDE = "cache_aside"
    WRITE_THROUGH = "write_through"
