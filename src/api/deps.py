"""FastAPI dependency wiring.

Kept in one place so routers declare *what* they need, not how it is built.
When the cache lands at M3, only this module changes -- the routers do not.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.engine import get_session
from src.repository.product import ProductRepository
from src.service.product import ProductService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_product_service(session: SessionDep) -> ProductService:
    """Build the product service for the current request.

    Args:
        session: The request-scoped database session.

    Returns:
        A service bound to this request's session.
    """
    return ProductService(ProductRepository(session))


ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]
