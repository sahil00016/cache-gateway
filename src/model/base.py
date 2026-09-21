"""Declarative base and mixins shared by every table."""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every table in this service."""


class TimestampMixin:
    """Adds server-generated created and updated timestamps.

    Defaults are server-side (``now()``) rather than Python-side so that rows
    inserted by a migration or by hand are stamped identically to rows inserted
    by the application.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
