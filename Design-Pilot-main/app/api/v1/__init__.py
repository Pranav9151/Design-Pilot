"""v1 API routes."""

from fastapi import APIRouter

from app.api.v1 import designs, designs_stream, health, materials

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(materials.router, prefix="/materials", tags=["materials"])
# SSE streaming: POST /api/v1/designs/stream
# Mounted BEFORE the /{design_id} routes to avoid path-param capture.
api_router.include_router(designs_stream.router, prefix="/designs", tags=["designs"])
api_router.include_router(designs.router, prefix="/designs", tags=["designs"])

__all__ = ["api_router"]
