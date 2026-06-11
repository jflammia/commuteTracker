"""OwnTracks ingest. Contract: ALWAYS return 200 with a JSON array body."""

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def make_ingest_router() -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/owntracks")
    async def ingest_owntracks(request: Request) -> JSONResponse:
        store = request.app.state.raw_store
        received_at = datetime.now(UTC).isoformat()
        body: bytes | None = None
        try:
            body = await request.body()
            try:
                payload = json.loads(body)
                record = {
                    "received_at": received_at,
                    "user": request.headers.get("X-Limit-U"),
                    "device": request.headers.get("X-Limit-D"),
                    "payload": payload,
                }
                store.append("owntracks", record)
            except (json.JSONDecodeError, UnicodeDecodeError):
                store.append(
                    "owntracks",
                    {"received_at": received_at, "raw": body.decode("utf-8", errors="replace")},
                    malformed=True,
                )
            try:
                await request.app.state.passthrough.forward(body, dict(request.headers))
            except Exception:
                log.exception("passthrough dispatch failed")
        except Exception:
            log.exception(
                "ingest failed — data may be lost (received_at=%s user=%s device=%s body[:500]=%r)",
                received_at,
                request.headers.get("X-Limit-U"),
                request.headers.get("X-Limit-D"),
                body[:500] if body is not None else None,
            )
        return JSONResponse(content=[], status_code=200)

    return router
