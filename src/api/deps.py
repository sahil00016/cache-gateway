"""FastAPI dependency wiring.

Kept in one place so routers declare *what* they need, not how it is built.
"""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.settings import Settings, get_settings
from src.db.engine import get_session
from src.db.redis import get_redis
from src.repository.product import ProductRepository
from src.repository.product_cache import ProductCacheRepository
from src.service.product import ProductService

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_product_cache(redis: RedisDep, settings: SettingsDep) -> ProductCacheRepository:
    """Build the product cache for the current request.

    Args:
        redis: The process-wide Redis client.
        settings: Validated application settings.

    Returns:
        A cache-aside repository bound to this request.
    """
    return ProductCacheRepository(redis, settings)


ProductCacheDep = Annotated[ProductCacheRepository, Depends(get_product_cache)]


def get_product_service(session: SessionDep, cache: ProductCacheDep) -> ProductService:
    """Build the product service for the current request.

    Args:
        session: The request-scoped database session.
        cache: The request-scoped cache-aside repository.

    Returns:
        A service bound to this request's session and cache.
    """
    return ProductService(ProductRepository(session), cache)


ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]
