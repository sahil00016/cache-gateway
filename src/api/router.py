"""Aggregates every versioned router into one mountable router."""

from fastapi import APIRouter

from src.api.v1 import admin, health, products

api_router = APIRouter()

# Probes are mounted unversioned: orchestrators and load balancers expect them
# at fixed paths, and they are not part of the client-facing API contract.
api_router.include_router(health.router)

api_router.include_router(products.router)
api_router.include_router(admin.router)
