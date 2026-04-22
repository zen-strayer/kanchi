"""Prometheus metrics endpoint."""

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def create_router(app_state) -> APIRouter:
    """Expose Prometheus metrics, gated behind auth when AUTH_ENABLED=true."""
    router = APIRouter()

    @router.get("/metrics")
    async def metrics(request: Request):
        config = app_state.config
        if config and config.auth_enabled and app_state.auth_dependencies:
            await app_state.auth_dependencies.require_user(request)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router
