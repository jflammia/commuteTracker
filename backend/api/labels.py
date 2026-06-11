"""Label events: archived as primitive data FIRST, then applied to derived.

Validation is strict — labels are human input through our own UI, so a
malformed payload is a bug, not data to preserve. Valid labels are appended
to the raw `labels` stream (same archive pipeline as GPS) before application;
rebuild replays them, so corrections are permanent."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

_SEGMENT_MODES = {"stationary", "walk", "vehicle", "train"}
_CONFIRMATIONS = {"confirmed", "wrong"}
_FLAGS = {"phantom", "ok"}


def _validate(body: dict) -> str | None:
    kind = body.get("type")
    if not isinstance(body.get("trip_id"), str):
        return "trip_id must be a string"
    if kind == "segment_mode":
        if not isinstance(body.get("seg_index"), int):
            return "segment_mode requires integer seg_index"
        if body.get("value") not in _SEGMENT_MODES:
            return f"value must be one of {sorted(_SEGMENT_MODES)}"
    elif kind == "train_match":
        if not isinstance(body.get("seg_index"), int):
            return "train_match requires integer seg_index"
        if body.get("value") not in _CONFIRMATIONS:
            return f"value must be one of {sorted(_CONFIRMATIONS)}"
    elif kind == "trip_flag":
        if body.get("value") not in _FLAGS:
            return f"value must be one of {sorted(_FLAGS)}"
    elif kind == "trip_reviewed":
        if not isinstance(body.get("value"), bool):
            return "trip_reviewed value must be a boolean"
    else:
        return "type must be one of segment_mode, train_match, trip_flag, trip_reviewed"
    return None


def make_labels_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/labels", status_code=201)
    async def post_label(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="request body must be valid JSON") from None
        error = _validate(body)
        if error is not None:
            raise HTTPException(status_code=400, detail=error)
        record = {"received_at": datetime.now(UTC).isoformat(), "payload": body}
        # A failure AFTER this append returns 500 but the label is archived — rebuild applies it.
        request.app.state.raw_store.append("labels", record)  # primitive first
        applied = request.app.state.runner.store.apply_label(body)
        return {"applied": applied}

    return router
