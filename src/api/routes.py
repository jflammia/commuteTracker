"""REST API routes for the Commute Tracker.

Thin HTTP layer over CommuteService. All business logic lives in service.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.service import CommuteService

router = APIRouter(prefix="/api/v1")

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

VALID_MODES = ["stationary", "waiting", "walking", "driving", "train"]


class LabelRequest(BaseModel):
    """A segment label correction."""

    commute_id: str = Field(..., examples=["2026-03-26-morning"])
    segment_id: int = Field(..., ge=0, examples=[2])
    original_mode: str = Field(
        ...,
        description=f"Classifier-assigned mode. One of: {', '.join(VALID_MODES)}",
        examples=["driving"],
    )
    corrected_mode: str = Field(
        ...,
        description=f"Correct mode. One of: {', '.join(VALID_MODES)}",
        examples=["train"],
    )
    notes: str = Field("", examples=["Was on the express train, classifier confused by tunnel GPS"])

    model_config = {"json_schema_extra": {"examples": [
        {
            "commute_id": "2026-03-26-morning",
            "segment_id": 2,
            "original_mode": "driving",
            "corrected_mode": "train",
            "notes": "Classifier confused by tunnel GPS jitter",
        }
    ]}}


class RebuildRequest(BaseModel):
    """Parameters for rebuilding derived data from raw GPS records."""

    since: str | None = Field(None, description="Start date inclusive (YYYY-MM-DD)", examples=["2026-03-01"])
    until: str | None = Field(None, description="End date inclusive (YYYY-MM-DD)", examples=["2026-03-31"])
    user: str | None = Field(None, description="Filter by OwnTracks user", examples=["jf"])
    device: str | None = Field(None, description="Filter by OwnTracks device", examples=["iphone"])
    clean: bool = Field(False, description="Delete existing Parquet files in range before rebuilding")
    dry_run: bool = Field(False, description="Preview what would be rebuilt without writing files")


class TrainRequest(BaseModel):
    """Parameters for ML model training."""

    max_depth: int = Field(10, ge=1, le=50, description="Maximum depth of the decision tree")
    test_fraction: float = Field(0.2, ge=0.0, le=0.5, description="Fraction of data held out for testing")


class QueryRequest(BaseModel):
    """A SQL query over derived Parquet data."""

    sql: str = Field(
        ...,
        description="SQL query using 'commute_data' as the table name. Powered by DuckDB.",
        examples=["SELECT commute_id, avg(speed_kmh) as avg_speed FROM commute_data GROUP BY commute_id"],
    )


class ApplyCorrectionsRequest(BaseModel):
    """Corrections to apply from a label review, filtered by confidence."""

    corrections: list[dict] = Field(
        ...,
        description="List of suggested corrections (from review endpoints). Each must have: commute_id, segment_id, original_mode, corrected_mode, confidence.",
    )
    min_confidence: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold. Corrections below this are skipped.",
    )


# ── Health ────────────────────────────────────────────────────────────────────


@router.get("/health", tags=["system"], summary="System health check")
def api_health():
    """Returns system status, record counts, label count, and available dates."""
    return get_service().health()


# ── Commutes ──────────────────────────────────────────────────────────────────


@router.get("/commutes", tags=["commutes"], summary="List all commutes")
def list_commutes():
    """List all detected commutes with summary statistics.

    Each commute includes: `commute_id`, `commute_direction`, `start_time`,
    `end_time`, `duration_min`, `total_distance_m`, and `point_count`.
    """
    return get_service().list_commutes()


@router.get("/commutes/{commute_id}", tags=["commutes"], summary="Get commute details")
def get_commute(commute_id: str):
    """Get full details for a commute: all GPS points, segment breakdown, and labels.

    Returns 404 if the commute ID is not found in derived data.
    """
    result = get_service().get_commute(commute_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Commute {commute_id} not found")
    return result


@router.get("/commutes/{commute_id}/segments", tags=["commutes"], summary="Get commute segments")
def get_segments(commute_id: str):
    """Get the segment breakdown for a commute.

    Each segment represents a leg in a single transport mode with:
    `segment_id`, `transport_mode`, `start_time`, `end_time`, `duration_min`,
    `distance_m`, `avg_speed_kmh`, `max_speed_kmh`.
    """
    return get_service().get_commute_segments(commute_id)


@router.get("/commutes/{commute_id}/points", tags=["commutes"], summary="Get commute GPS points")
def get_points(commute_id: str):
    """Get all GPS points for a commute, ordered chronologically.

    Each point has: `lat`, `lon`, `speed_kmh`, `timestamp`, `transport_mode`,
    `segment_id`, `distance_m`, `time_delta_s`, and more.
    """
    return get_service().get_commute_points(commute_id)


# ── Analytics ─────────────────────────────────────────────────────────────────


@router.get("/stats", tags=["analytics"], summary="Aggregate statistics")
def get_stats():
    """Aggregate statistics across all commutes.

    Broken down by commute direction. Includes averages, min/max, and
    standard deviation for duration.
    """
    return get_service().get_stats()


@router.get("/daily/{day}", tags=["analytics"], summary="Daily data points")
def get_daily(day: str):
    """Get all processed data points for a specific date."""
    return get_service().get_daily_summary(day)


@router.post("/query", tags=["analytics"], summary="Run SQL query")
def run_query(req: QueryRequest):
    """Run an arbitrary SQL query over derived Parquet data via DuckDB.

    Use `commute_data` as the table name. Returns results as a JSON array.

    Available columns include: `lat`, `lon`, `speed_kmh`, `timestamp`,
    `distance_m`, `time_delta_s`, `is_stationary`, `commute_id`,
    `commute_direction`, `transport_mode`, `segment_id`.
    """
    try:
        return get_service().query_derived(req.sql)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/raw/stats", tags=["analytics"], summary="Raw data statistics")
def get_raw_stats():
    """Get statistics about raw GPS data in the database.

    Returns total record count and unsynced count (records not yet backed up to S3).
    """
    return get_service().get_raw_stats()


@router.get("/raw/count", tags=["analytics"], summary="Count raw records with filters")
def count_raw_records(
    since: str | None = Query(default=None, description="Start date inclusive (YYYY-MM-DD)"),
    until: str | None = Query(default=None, description="End date inclusive (YYYY-MM-DD)"),
    user: str | None = Query(default=None, description="Filter by OwnTracks user"),
    device: str | None = Query(default=None, description="Filter by OwnTracks device"),
):
    """Count raw GPS records matching the given filters.

    Useful for previewing how many records will be processed before
    triggering a rebuild.
    """
    return get_service().count_raw_records(since=since, until=until, user=user, device=device)


@router.get("/dates", tags=["analytics"], summary="List available dates")
def list_dates():
    """List all dates that have derived (processed) data available.

    Returns an array of date strings from processed Parquet file names.
    """
    return get_service().list_dates()


# ── Labels ────────────────────────────────────────────────────────────────────


@router.get("/labels", tags=["labels"], summary="List labels")
def list_labels(
    commute_id: str | None = Query(default=None, description="Filter by commute ID"),
):
    """List all segment label corrections, optionally filtered by commute.

    Labels are user corrections to the automatic transport mode classifier.
    Each label records the original mode, corrected mode, notes, and timestamp.
    """
    return get_service().list_labels(commute_id)


@router.post("/labels", tags=["labels"], summary="Add a label")
def add_label(req: LabelRequest):
    """Add or update a segment label correction.

    If a label already exists for this commute + segment, it is updated.
    Labels persist in the database and are applied during rebuild.
    """
    return get_service().add_label(
        commute_id=req.commute_id,
        segment_id=req.segment_id,
        original_mode=req.original_mode,
        corrected_mode=req.corrected_mode,
        notes=req.notes,
    )


@router.get("/labels/corrections", tags=["labels"], summary="Corrections lookup map")
def get_corrections():
    """Get all corrections as a flat lookup map.

    Returns `{"commute_id:segment_id": "corrected_mode", ...}`.
    Efficient for frontends to overlay corrections on segment displays.
    """
    return get_service().get_corrections_map()


@router.post("/labels/bulk", tags=["labels"], summary="Bulk add labels")
def add_labels_bulk(labels: list[LabelRequest]):
    """Add multiple segment label corrections in one request.

    Useful for "mark all segments as correct" or batch correction workflows.
    """
    return get_service().add_labels_bulk([lb.model_dump() for lb in labels])


@router.get("/labels/export", tags=["labels"], summary="Export all labels")
def export_labels():
    """Export all labels as a JSON document.

    Suitable for backup, sharing, or ML training data preparation.
    """
    return get_service().export_labels()


# ── Label Intelligence ───────────────────────────────────────────────────────


@router.get(
    "/labels/analyze/{commute_id}/{segment_id}",
    tags=["label-intelligence"],
    summary="Analyze a segment (low-level)",
)
def analyze_segment(commute_id: str, segment_id: int):
    """Deep analysis of a single segment with mismatch detection.

    **Low-level labeling operation.** Use when you need to understand exactly
    what's happening in one segment before correcting it.

    Returns speed statistics (mean, median, max, min, std), duration, distance,
    neighboring segment modes, and mismatch analysis with a suggested correction
    and confidence score.
    """
    result = get_service().analyze_segment(commute_id, segment_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get(
    "/labels/review/{commute_id}",
    tags=["label-intelligence"],
    summary="Review a commute (mid-level)",
)
def review_commute(commute_id: str):
    """Review all segments in a commute and flag suspicious classifications.

    **Mid-level labeling operation.** Analyzes every segment, checks speed
    profiles against expected ranges, and returns flagged segments sorted by
    confidence of misclassification.

    The `suggested_corrections` array in the response can be passed directly
    to `POST /labels/apply` to apply them.
    """
    result = get_service().review_commute_segments(commute_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get(
    "/labels/review",
    tags=["label-intelligence"],
    summary="Review recent commutes (high-level)",
)
def review_recent(
    n: int = Query(default=5, ge=1, le=50, description="Number of recent commutes to review"),
    direction: str | None = Query(default=None, description="Filter by commute direction (e.g. to_work, to_home)"),
):
    """Review recent commutes for systematic misclassification patterns.

    **High-level labeling operation.** Reviews the last N commutes, aggregates
    mismatch patterns (e.g., "driving → train appears in 4 commutes"), and
    returns batch corrections sorted by confidence.

    The `suggested_corrections` array can be passed to `POST /labels/apply`.
    The `systematic_patterns` array shows recurring misclassification types.
    """
    result = get_service().review_recent_commutes(n=n, direction=direction)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post(
    "/labels/apply",
    tags=["label-intelligence"],
    summary="Apply suggested corrections",
)
def apply_corrections(req: ApplyCorrectionsRequest):
    """Apply corrections from a review, filtered by confidence threshold.

    Takes the `suggested_corrections` output from the review endpoints and
    applies them as durable label corrections. Only corrections with
    `confidence >= min_confidence` are applied; the rest are returned in
    the `skipped` array.

    **Workflow:**
    1. Call `GET /labels/review/{commute_id}` or `GET /labels/review`
    2. Inspect the `suggested_corrections` in the response
    3. Pass them here with your desired `min_confidence`
    """
    return get_service().apply_suggested_corrections(
        corrections=req.corrections,
        min_confidence=req.min_confidence,
    )


# ── Processing ────────────────────────────────────────────────────────────────


@router.post("/rebuild", tags=["processing"], summary="Rebuild derived data")
def rebuild_derived(req: RebuildRequest):
    """Rebuild derived Parquet files from raw GPS data in the database.

    Re-runs the full pipeline: enrichment, commute detection, segmentation,
    and transport mode classification. Existing label corrections are applied.

    Use `dry_run: true` to preview what would be rebuilt without writing files.
    """
    return get_service().rebuild_derived(
        since=req.since,
        until=req.until,
        user=req.user,
        device=req.device,
        clean=req.clean,
        dry_run=req.dry_run,
    )


# ── ML ────────────────────────────────────────────────────────────────────────


@router.post("/ml/train", tags=["ml"], summary="Train ML model")
def train_model(req: TrainRequest | None = None):
    """Train the ML transport mode classifier from labeled data.

    Uses a decision tree trained on 14 features (speed, acceleration,
    bearing changes, rolling stats, temporal features). Requires labeled
    segments — add labels first via the labels endpoints.

    Returns accuracy metrics and feature importances.
    """
    req = req or TrainRequest()
    return get_service().train_model(
        max_depth=req.max_depth,
        test_fraction=req.test_fraction,
    )


@router.get("/ml/evaluate", tags=["ml"], summary="Evaluate classifier")
def evaluate_classifier():
    """Compare the ensemble classifier's output against user-provided labels.

    Returns accuracy metrics showing where the classifier agrees or disagrees
    with human corrections. Useful for understanding classifier weaknesses.
    """
    return get_service().evaluate_classifier()
