"""REST API routes for the Commute Tracker.

Thin HTTP layer over CommuteService. All business logic lives in service.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.service import CommuteService

router = APIRouter(prefix="/api/v1", tags=["api"])

# Service is initialized once when the router is first used.
_service: CommuteService | None = None


def get_service() -> CommuteService:
    global _service
    if _service is None:
        _service = CommuteService()
    return _service


def set_service(service: CommuteService) -> None:
    """Inject a service instance (used during app lifespan)."""
    global _service
    _service = service


# ── Request/Response Models ───────────────────────────────────────────────────


class LabelRequest(BaseModel):
    commute_id: str
    segment_id: int
    original_mode: str
    corrected_mode: str
    notes: str = ""


class RebuildRequest(BaseModel):
    since: str | None = None
    until: str | None = None
    user: str | None = None
    device: str | None = None
    clean: bool = False
    dry_run: bool = False


class TrainRequest(BaseModel):
    max_depth: int = 10
    test_fraction: float = 0.2


class QueryRequest(BaseModel):
    sql: str


# ── Health ────────────────────────────────────────────────────────────────────


@router.get("/health")
def api_health():
    return get_service().health()


# ── Commutes ──────────────────────────────────────────────────────────────────


@router.get("/commutes")
def list_commutes():
    """List all detected commutes with summary stats."""
    return get_service().list_commutes()


@router.get("/commutes/{commute_id}")
def get_commute(commute_id: str):
    """Get full details for a commute: points, segments, and labels."""
    result = get_service().get_commute(commute_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Commute {commute_id} not found")
    return result


@router.get("/commutes/{commute_id}/segments")
def get_segments(commute_id: str):
    """Get segment breakdown for a commute."""
    return get_service().get_commute_segments(commute_id)


@router.get("/commutes/{commute_id}/points")
def get_points(commute_id: str):
    """Get all GPS points for a commute."""
    return get_service().get_commute_points(commute_id)


# ── Analytics ─────────────────────────────────────────────────────────────────


@router.get("/stats")
def get_stats():
    """Aggregate statistics across all commutes."""
    return get_service().get_stats()


@router.get("/daily/{day}")
def get_daily(day: str):
    """Get all data points for a specific date (YYYY-MM-DD)."""
    return get_service().get_daily_summary(day)


@router.post("/query")
def run_query(req: QueryRequest):
    """Run a SQL query over derived Parquet data.

    Use 'commute_data' as the table name.
    """
    try:
        return get_service().query_derived(req.sql)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/raw/stats")
def get_raw_stats():
    """Get statistics about raw GPS data in the database."""
    return get_service().get_raw_stats()


@router.get("/raw/count")
def count_raw_records(
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    user: str | None = Query(default=None),
    device: str | None = Query(default=None),
):
    """Count raw GPS records matching filters. Useful for rebuild preview."""
    return get_service().count_raw_records(since=since, until=until, user=user, device=device)


@router.get("/dates")
def list_dates():
    """List all dates that have derived (processed) data available."""
    return get_service().list_dates()


# ── Labels ────────────────────────────────────────────────────────────────────


@router.get("/labels")
def list_labels(commute_id: str | None = Query(default=None)):
    """List all segment labels, optionally filtered by commute."""
    return get_service().list_labels(commute_id)


@router.post("/labels")
def add_label(req: LabelRequest):
    """Add or update a segment label correction."""
    return get_service().add_label(
        commute_id=req.commute_id,
        segment_id=req.segment_id,
        original_mode=req.original_mode,
        corrected_mode=req.corrected_mode,
        notes=req.notes,
    )


@router.get("/labels/corrections")
def get_corrections():
    """Get all corrections as a lookup map: 'commute_id:segment_id' -> corrected_mode.

    Efficient for frontends to overlay corrections on segment displays.
    """
    return get_service().get_corrections_map()


@router.post("/labels/bulk")
def add_labels_bulk(labels: list[LabelRequest]):
    """Add multiple segment label corrections at once.

    Useful for 'mark all correct' or batch correction workflows.
    """
    return get_service().add_labels_bulk([lb.model_dump() for lb in labels])


@router.get("/labels/export")
def export_labels():
    """Export all labels as JSON."""
    return get_service().export_labels()


@router.get("/labels/analyze/{commute_id}/{segment_id}")
def analyze_segment(commute_id: str, segment_id: int):
    """Deep analysis of a single segment with mismatch detection.

    Low-level labeling: returns speed stats, context, and whether
    the classification looks correct. Use before correcting a segment.
    """
    result = get_service().analyze_segment(commute_id, segment_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/labels/review/{commute_id}")
def review_commute(commute_id: str):
    """Review all segments in a commute and flag suspicious classifications.

    Mid-level labeling: checks every segment's speed profile and returns
    flagged segments with suggested corrections sorted by confidence.
    """
    result = get_service().review_commute_segments(commute_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/labels/review")
def review_recent(
    n: int = Query(default=5, ge=1, le=50),
    direction: str | None = Query(default=None),
):
    """Review recent commutes for systematic misclassification patterns.

    High-level labeling: reviews last N commutes, finds patterns, and
    returns batch corrections sorted by confidence.
    """
    result = get_service().review_recent_commutes(n=n, direction=direction)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


class ApplyCorrectionsRequest(BaseModel):
    corrections: list[dict]
    min_confidence: float = 0.7


@router.post("/labels/apply")
def apply_corrections(req: ApplyCorrectionsRequest):
    """Apply suggested corrections from a review, filtered by confidence.

    Takes the suggested_corrections from review endpoints and applies
    them as labels. Only corrections >= min_confidence are applied.
    """
    return get_service().apply_suggested_corrections(
        corrections=req.corrections,
        min_confidence=req.min_confidence,
    )


# ── Processing ────────────────────────────────────────────────────────────────


@router.post("/rebuild")
def rebuild_derived(req: RebuildRequest):
    """Rebuild derived Parquet files from the database."""
    return get_service().rebuild_derived(
        since=req.since,
        until=req.until,
        user=req.user,
        device=req.device,
        clean=req.clean,
        dry_run=req.dry_run,
    )


# ── ML ────────────────────────────────────────────────────────────────────────


@router.post("/ml/train")
def train_model(req: TrainRequest | None = None):
    """Train the ML baseline model from labeled data."""
    req = req or TrainRequest()
    return get_service().train_model(
        max_depth=req.max_depth,
        test_fraction=req.test_fraction,
    )


@router.get("/ml/evaluate")
def evaluate_classifier():
    """Compare ensemble classifier output against user labels."""
    return get_service().evaluate_classifier()
