"""HTTP client for the Commute Tracker REST API.

Used by dashboard pages to fetch data from the API instead of importing
store classes directly. This decouples the frontend from the backend.

The base URL defaults to the local receiver (same container or host).
Override with the COMMUTE_API_URL environment variable.
"""

from __future__ import annotations

import os

import httpx
import polars as pl

API_BASE = os.environ.get("COMMUTE_API_URL", "http://localhost:8080/api/v1")
_TIMEOUT = 30.0


def _client() -> httpx.Client:
    return httpx.Client(base_url=API_BASE, timeout=_TIMEOUT)


def _get(path: str, **params) -> dict | list:
    with _client() as c:
        resp = c.get(path, params={k: v for k, v in params.items() if v is not None})
        resp.raise_for_status()
        return resp.json()


def _post(path: str, json=None) -> dict | list:
    with _client() as c:
        resp = c.post(path, json=json)
        resp.raise_for_status()
        return resp.json()


def _to_df(records: list[dict]) -> pl.DataFrame:
    """Convert a list of JSON records to a Polars DataFrame."""
    if not records:
        return pl.DataFrame()
    return pl.DataFrame(records)


# ── Health & Dates ────────────────────────────────────────────────────────────

def get_health() -> dict:
    return _get("/health")


def list_dates() -> list[str]:
    return _get("/dates")


# ── Commutes ──────────────────────────────────────────────────────────────────

def get_commutes() -> pl.DataFrame:
    """List all commutes as a Polars DataFrame."""
    records = _get("/commutes")
    if not records:
        return pl.DataFrame()
    df = pl.DataFrame(records)
    # Parse datetime columns
    if "start_time" in df.columns:
        df = df.with_columns(pl.col("start_time").str.to_datetime(strict=False))
    if "end_time" in df.columns:
        df = df.with_columns(pl.col("end_time").str.to_datetime(strict=False))
    return df


def get_commute(commute_id: str) -> dict | None:
    """Get full commute details (points, segments, labels)."""
    try:
        return _get(f"/commutes/{commute_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


def get_segments(commute_id: str) -> pl.DataFrame:
    """Get segments for a commute as a Polars DataFrame."""
    records = _get(f"/commutes/{commute_id}/segments")
    if not records:
        return pl.DataFrame()
    df = pl.DataFrame(records)
    if "start_time" in df.columns:
        df = df.with_columns(pl.col("start_time").str.to_datetime(strict=False))
    if "end_time" in df.columns:
        df = df.with_columns(pl.col("end_time").str.to_datetime(strict=False))
    return df


def get_commute_points(commute_id: str) -> pl.DataFrame:
    """Get all GPS points for a commute as a Polars DataFrame."""
    records = _get(f"/commutes/{commute_id}/points")
    if not records:
        return pl.DataFrame()
    df = pl.DataFrame(records)
    if "timestamp" in df.columns:
        df = df.with_columns(pl.col("timestamp").str.to_datetime(strict=False))
    return df


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_stats() -> pl.DataFrame:
    """Get aggregate stats as a Polars DataFrame."""
    data = _get("/stats")
    if not data:
        return pl.DataFrame()
    # Stats may be a single dict or {"rows": [...]}
    if "rows" in data:
        return pl.DataFrame(data["rows"])
    return pl.DataFrame([data])


def get_daily_summary(day: str) -> pl.DataFrame:
    """Get all points for a date as a Polars DataFrame."""
    records = _get(f"/daily/{day}")
    if not records:
        return pl.DataFrame()
    df = pl.DataFrame(records)
    if "timestamp" in df.columns:
        df = df.with_columns(pl.col("timestamp").str.to_datetime(strict=False))
    return df


# ── Raw Data ──────────────────────────────────────────────────────────────────

def count_raw_records(
    since: str | None = None,
    until: str | None = None,
    user: str | None = None,
    device: str | None = None,
) -> dict:
    return _get("/raw/count", since=since, until=until, user=user, device=device)


# ── Labels ────────────────────────────────────────────────────────────────────

def get_labels(commute_id: str | None = None) -> list[dict]:
    return _get("/labels", commute_id=commute_id)


def add_label(
    commute_id: str,
    segment_id: int,
    original_mode: str,
    corrected_mode: str,
    notes: str = "",
) -> dict:
    return _post("/labels", json={
        "commute_id": commute_id,
        "segment_id": segment_id,
        "original_mode": original_mode,
        "corrected_mode": corrected_mode,
        "notes": notes,
    })


def add_labels_bulk(labels: list[dict]) -> list[dict]:
    return _post("/labels/bulk", json=labels)


def get_corrections_map() -> dict[str, str]:
    return _get("/labels/corrections")


def export_labels() -> dict:
    return _get("/labels/export")


def label_count() -> int:
    health = _get("/health")
    return health.get("label_count", 0)


# ── Label Intelligence ───────────────────────────────────────────────────────

def analyze_segment(commute_id: str, segment_id: int) -> dict:
    """Deep analysis of a single segment with mismatch detection."""
    return _get(f"/labels/analyze/{commute_id}/{segment_id}")


def review_commute(commute_id: str) -> dict:
    """Review all segments in a commute and flag suspicious classifications."""
    return _get(f"/labels/review/{commute_id}")


def review_recent(n: int = 5, direction: str | None = None) -> dict:
    """Review recent commutes for systematic misclassification patterns."""
    return _get("/labels/review", n=n, direction=direction)


def apply_corrections(corrections: list[dict], min_confidence: float = 0.7) -> dict:
    """Apply suggested corrections from a review."""
    return _post("/labels/apply", json={
        "corrections": corrections,
        "min_confidence": min_confidence,
    })


# ── Processing ────────────────────────────────────────────────────────────────

def rebuild_derived(
    since: str | None = None,
    until: str | None = None,
    user: str | None = None,
    device: str | None = None,
    clean: bool = False,
    dry_run: bool = False,
) -> dict:
    return _post("/rebuild", json={
        "since": since,
        "until": until,
        "user": user,
        "device": device,
        "clean": clean,
        "dry_run": dry_run,
    })
