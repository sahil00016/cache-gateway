"""Product request and response models."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProductRead(BaseModel):
    """A product as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    description: str | None
    price_cents: int = Field(description="Price in minor units, to avoid float money.")
    category: str
    created_at: datetime
    updated_at: datetime


class ProductWrite(BaseModel):
    """Fields accepted when creating or replacing a product."""

    sku: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    price_cents: int = Field(ge=0)
    category: str = Field(min_length=1, max_length=64)


class ProductUpdate(BaseModel):
    """Fields accepted when updating a product."""

    sku: str | None = Field(None, min_length=1, max_length=32)
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    price_cents: int | None = Field(None, ge=0)
    category: str | None = Field(None, min_length=1, max_length=64)
