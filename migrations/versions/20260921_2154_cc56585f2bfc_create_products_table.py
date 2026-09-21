"""create products table

The subject table of this service: deliberately ordinary, read-heavy, and
large. `ix_products_category_live` is partial on `deleted_at IS NULL` so the
planner never walks tombstoned rows and the index stays small.

Revision ID: cc56585f2bfc
Revises:
Created: 2026-09-21 21:54:03.088948
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cc56585f2bfc"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sku", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_cents", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )
    op.create_index(
        "ix_products_category_live",
        "products",
        ["category"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        "ix_products_category_live",
        table_name="products",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("products")
