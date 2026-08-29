"""Aggregate router for API v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import addresses, admin, auth, cart, orders, products

api_router = APIRouter()

# Order matters only for documentation grouping; FastAPI matches on path.
api_router.include_router(auth.router)
api_router.include_router(products.router)
api_router.include_router(cart.router)
api_router.include_router(addresses.router)
api_router.include_router(orders.router)
api_router.include_router(admin.router)
