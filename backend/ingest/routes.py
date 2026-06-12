"""OwnTracks ingest. Contract: ALWAYS return 200 with a JSON array body."""

import base64
import json
import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def _pub_auth_ok(authorization_header: str, username: str | None, password: str | None) -> bool:
    """Validate Basic Auth against OWNTRACKS_USERNAME/PASSWORD (ported from the
    legacy prod fork). True if auth is disabled (either credential unset) or the
    header carries valid Basic Auth matching the configured creds (timing-safe).
    Callers must NOT turn False into a 4xx — OwnTracks discards on any 4xx;
    instead return 200 with no write and a WARNING so bad posts are black-holed.
    """
    if not username or not password:
        return True
    if not authorization_header or not authorization_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization_header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, sep, pw = decoded.partition(":")
    if not sep:
        return False
    return secrets.compare_digest(user, username) and secrets.compare_digest(pw, password)


def make_ingest_router() -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/owntracks")
    async def ingest_owntracks(request: Request) -> JSONResponse:
        settings = request.app.state.settings
        if not _pub_auth_ok(
            request.headers.get("authorization", ""),
            settings.owntracks_username,
            settings.owntracks_password,
        ):
            log.warning(
                "Unauthenticated /pub POST from %s/%s; payload dropped (returning 200 "
                "per OwnTracks protocol)",
                request.headers.get("X-Limit-U"),
                request.headers.get("X-Limit-D"),
            )
            return JSONResponse(content=[], status_code=200)
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
                try:
                    request.app.state.runner.process_payload(payload, received_at=received_at)
                except Exception:
                    log.exception("engine processing failed — raw is safe; rebuild recovers")
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

    # Drop-in alias: OwnTracks is still pointed at /pub on existing deployments.
    # Binding the same handler to /pub means no OwnTracks reconfiguration is
    # needed when cutting over from the legacy backend.
    router.post("/pub")(ingest_owntracks)

    return router
