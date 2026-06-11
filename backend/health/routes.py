from datetime import UTC, datetime

from fastapi import APIRouter, Request

from backend.health.ingestion import ingestion_snapshot
from backend.health.sources import sources_snapshot


def make_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/health/ingestion")
    async def health_ingestion(request: Request) -> dict:
        return ingestion_snapshot(request.app.state.settings, now_iso=datetime.now(UTC).isoformat())

    @router.get("/api/health/sources")
    async def health_sources(request: Request) -> list[dict]:
        return sources_snapshot(request.app.state.settings, now_iso=datetime.now(UTC).isoformat())

    return router
