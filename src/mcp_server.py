"""MCP server for the Commute Tracker.

Exposes all commute tracking operations as MCP tools, resources, and prompts
for LLM integration. Uses the official mcp Python SDK with FastMCP.

Transport: Streamable HTTP at /mcp (stateless, JSON responses).

Resources = read-only data (analogous to GET)
Tools = actions with side effects (analogous to POST)
Prompts = reusable LLM interaction templates (user-invoked)
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP, Context

from src.api.service import CommuteService
from src.config import DATABASE_URL
from src.storage.database import Database

logger = logging.getLogger(__name__)

# Module-level service, initialized during lifespan
_service: CommuteService | None = None


def get_service() -> CommuteService:
    """Get the service instance. Lazy-init if not set by lifespan."""
    global _service
    if _service is None:
        db = Database(DATABASE_URL)
        db.create_tables()
        _service = CommuteService(db=db)
    return _service


def set_service(service: CommuteService) -> None:
    """Inject a service (used when mounted in the main app)."""
    global _service
    _service = service


@dataclass
class MCPLifespan:
    service: CommuteService


@asynccontextmanager
async def mcp_lifespan(server: FastMCP):
    """Initialize shared resources for the MCP server."""
    service = get_service()
    logger.info("MCP server initialized")
    try:
        yield MCPLifespan(service=service)
    finally:
        logger.info("MCP server shutting down")


mcp = FastMCP(
    "Commute Tracker",
    instructions=(
        "Commute tracking system that collects GPS data from OwnTracks, "
        "segments commutes into legs (walk, drive, train, waiting), and "
        "provides analytics for schedule optimization. Use resources to "
        "read data and tools to perform actions."
    ),
    lifespan=mcp_lifespan,
    stateless_http=True,
    json_response=True,
)


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCES — read-only data (no Context parameter)
# ══════════════════════════════════════════════════════════════════════════════


@mcp.resource("commutes://list")
def resource_list_commutes() -> str:
    """List all detected commutes with summary stats (direction, duration, distance).

    Returns a JSON array of commute summaries. Each has commute_id,
    start_time, duration_min, total_distance_m, commute_direction, and
    segment count.
    """
    return json.dumps(get_service().list_commutes(), indent=2)


@mcp.resource("commutes://{commute_id}")
def resource_get_commute(commute_id: str) -> str:
    """Get full details for a commute: all GPS points, segments, and any user labels.

    The commute_id is typically a date-based identifier like '2026-03-26_am_001'.
    """
    result = get_service().get_commute(commute_id)
    if result is None:
        return json.dumps({"error": f"Commute {commute_id} not found"})
    return json.dumps(result, indent=2)


@mcp.resource("commutes://{commute_id}/segments")
def resource_get_segments(commute_id: str) -> str:
    """Get the segment breakdown for a commute.

    Each segment represents a leg of the commute (walking, driving, train,
    waiting, or stationary) with start/end times, duration, distance, and
    average speed.
    """
    return json.dumps(get_service().get_commute_segments(commute_id), indent=2)


@mcp.resource("commutes://{commute_id}/points")
def resource_get_points(commute_id: str) -> str:
    """Get all GPS points for a commute, ordered chronologically.

    Each point has lat, lon, speed_kmh, timestamp, transport_mode, and
    segment_id.
    """
    return json.dumps(get_service().get_commute_points(commute_id), indent=2)


@mcp.resource("stats://overview")
def resource_get_stats() -> str:
    """Aggregate statistics across all commutes.

    Includes averages, min/max, and counts for duration and distance,
    broken down by direction (to_work, to_home).
    """
    return json.dumps(get_service().get_stats(), indent=2)


@mcp.resource("stats://raw")
def resource_raw_stats() -> str:
    """Statistics about raw GPS data in the database.

    Shows total record count and how many are not yet synced to S3 backup.
    """
    return json.dumps(get_service().get_raw_stats(), indent=2)


@mcp.resource("stats://health")
def resource_health() -> str:
    """System health: database status, record counts, sync state, available dates."""
    return json.dumps(get_service().health(), indent=2)


@mcp.resource("daily://{day}")
def resource_daily(day: str) -> str:
    """Get all processed data points for a specific date.

    Day format: YYYY-MM-DD. Returns all GPS points with enriched fields
    (speed, distance, transport mode, segment, commute assignment).
    """
    return json.dumps(get_service().get_daily_summary(day), indent=2)


@mcp.resource("dates://list")
def resource_list_dates() -> str:
    """List all dates that have processed commute data available.

    Returns a JSON array of date strings (from Parquet file names).
    Useful for knowing which days have data before querying.
    """
    return json.dumps(get_service().list_dates(), indent=2)


@mcp.resource("labels://corrections")
def resource_corrections_map() -> str:
    """Get all label corrections as a flat lookup map.

    Returns a JSON object mapping 'commute_id:segment_id' to the corrected
    transport mode. Efficient for checking which segments have been corrected.
    """
    return json.dumps(get_service().get_corrections_map(), indent=2)


@mcp.resource("labels://list")
def resource_list_labels() -> str:
    """List all user-provided segment label corrections.

    Labels override the automatic transport mode classifier. Each label
    records the original mode, the corrected mode, and optional notes.
    """
    return json.dumps(get_service().list_labels(), indent=2)


@mcp.resource("labels://{commute_id}")
def resource_labels_for_commute(commute_id: str) -> str:
    """Get label corrections for a specific commute."""
    return json.dumps(get_service().list_labels(commute_id), indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — actions with side effects (use Context for lifespan access)
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def query_commute_data(sql: str, ctx: Context) -> str:
    """Run a SQL query over all processed commute data.

    The data is stored as Parquet files and queried via DuckDB. Use
    'commute_data' as the table name.

    Available columns include: lat, lon, speed_kmh, timestamp, distance_m,
    time_delta_s, is_stationary, commute_id, commute_direction, transport_mode,
    segment_id, and more.

    Examples:
    - "SELECT commute_id, count(*) as points FROM commute_data GROUP BY commute_id"
    - "SELECT avg(speed_kmh) FROM commute_data WHERE transport_mode = 'train'"
    - "SELECT commute_direction, avg(duration_min) FROM commute_data GROUP BY commute_direction"
    """
    service = ctx.request_context.lifespan_context.service
    try:
        results = service.query_derived(sql)
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def add_segment_label(
    commute_id: str,
    segment_id: int,
    original_mode: str,
    corrected_mode: str,
    ctx: Context,
    notes: str = "",
) -> str:
    """Correct the transport mode classification for a commute segment.

    Use this when the automatic classifier got a segment wrong. For example,
    if a segment was classified as 'driving' but was actually 'train'.

    Valid modes: stationary, waiting, walking, driving, train.

    The correction is stored durably in the database and will be applied
    when derived data is rebuilt.

    Args:
        commute_id: The commute identifier (e.g. '2026-03-26_am_001')
        segment_id: The segment number within the commute (0-indexed)
        original_mode: What the classifier assigned
        corrected_mode: What the segment actually is
        notes: Optional explanation for the correction
    """
    service = ctx.request_context.lifespan_context.service
    result = service.add_label(
        commute_id=commute_id,
        segment_id=segment_id,
        original_mode=original_mode,
        corrected_mode=corrected_mode,
        notes=notes,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def add_segment_labels_bulk(
    labels: list[dict],
    ctx: Context,
) -> str:
    """Add multiple segment label corrections at once.

    Useful for batch corrections or marking all segments as correct.
    Each item in the list must have: commute_id, segment_id, original_mode,
    corrected_mode. Optional: notes.

    Example:
    [
        {"commute_id": "2026-03-26_am_001", "segment_id": 0,
         "original_mode": "driving", "corrected_mode": "driving"},
        {"commute_id": "2026-03-26_am_001", "segment_id": 1,
         "original_mode": "stationary", "corrected_mode": "waiting"}
    ]
    """
    service = ctx.request_context.lifespan_context.service
    results = service.add_labels_bulk(labels)
    return json.dumps(results, indent=2)


@mcp.tool()
def count_raw_records(
    ctx: Context,
    since: str | None = None,
    until: str | None = None,
    user: str | None = None,
    device: str | None = None,
) -> str:
    """Count raw GPS records matching filters.

    Useful for previewing how many records will be processed before
    triggering a rebuild. Returns the count and the filters applied.

    Args:
        since: Start date inclusive (YYYY-MM-DD)
        until: End date inclusive (YYYY-MM-DD)
        user: Filter by OwnTracks user
        device: Filter by OwnTracks device
    """
    service = ctx.request_context.lifespan_context.service
    result = service.count_raw_records(since=since, until=until, user=user, device=device)
    return json.dumps(result, indent=2)


@mcp.tool()
def rebuild_derived_data(
    ctx: Context,
    since: str | None = None,
    until: str | None = None,
    user: str | None = None,
    device: str | None = None,
    clean: bool = False,
    dry_run: bool = False,
) -> str:
    """Rebuild processed Parquet files from raw GPS data in the database.

    Re-runs the full pipeline: enrichment, commute detection, segmentation,
    and transport mode classification. Label corrections from the database
    are applied automatically.

    Use this after adding labels, changing classifier config, or to
    reprocess a specific date range.

    Args:
        since: Start date inclusive (YYYY-MM-DD)
        until: End date inclusive (YYYY-MM-DD)
        user: Filter by OwnTracks user
        device: Filter by OwnTracks device
        clean: Delete existing Parquet files in range before rebuilding
        dry_run: Preview what would be rebuilt without writing files
    """
    service = ctx.request_context.lifespan_context.service
    result = service.rebuild_derived(
        since=since,
        until=until,
        user=user,
        device=device,
        clean=clean,
        dry_run=dry_run,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def train_ml_model(
    ctx: Context,
    max_depth: int = 10,
    test_fraction: float = 0.2,
) -> str:
    """Train the machine learning model for transport mode classification.

    Uses labeled commute segments as training data. The model (a decision tree)
    learns from 14 features including speed, acceleration, bearing changes,
    rolling statistics, and temporal features.

    Requires at least some labeled segments (use add_segment_label first).
    Returns accuracy metrics and feature importances.

    Args:
        max_depth: Maximum depth of the decision tree (higher = more complex)
        test_fraction: Fraction of data held out for testing (0.0-1.0)
    """
    service = ctx.request_context.lifespan_context.service
    try:
        result = service.train_model(max_depth=max_depth, test_fraction=test_fraction)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def analyze_segment(
    commute_id: str,
    segment_id: int,
    ctx: Context,
) -> str:
    """Analyze a single segment's speed profile and detect classification mismatches.

    LOW-LEVEL labeling: Use this when you want to deeply inspect one specific
    segment before deciding whether to correct it. Returns speed statistics
    (mean, median, max, min, std), duration, distance, neighboring segment
    modes, and mismatch detection with a suggested correction and confidence.

    Best for:
    - Investigating a specific suspicious segment
    - Understanding why a segment was classified a certain way
    - Getting detailed evidence before making a correction

    Args:
        commute_id: The commute identifier (e.g. '2026-03-26_am_001')
        segment_id: The segment number within the commute (0-indexed)
    """
    service = ctx.request_context.lifespan_context.service
    result = service.analyze_segment(commute_id, segment_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def review_commute_labels(
    commute_id: str,
    ctx: Context,
) -> str:
    """Review all segments in a commute and flag suspicious classifications.

    MID-LEVEL labeling: Use this to efficiently review an entire commute.
    Checks every segment's speed profile against expected ranges and returns
    flagged segments sorted by confidence of misclassification, along with
    ready-to-apply suggested corrections.

    Best for:
    - Reviewing a specific commute after processing
    - Finding all misclassifications in one commute at once
    - Getting a list of corrections you can apply with apply_label_corrections

    The response includes:
    - all_segments: every segment with its analysis
    - flagged_segments: only the suspicious ones, sorted by confidence
    - suggested_corrections: ready to pass to apply_label_corrections
    """
    service = ctx.request_context.lifespan_context.service
    result = service.review_commute_segments(commute_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def review_recent_labels(
    ctx: Context,
    n: int = 5,
    direction: str | None = None,
) -> str:
    """Review recent commutes for systematic misclassification patterns.

    HIGH-LEVEL labeling: Use this for batch review across multiple commutes.
    Analyzes the last N commutes, aggregates mismatch patterns, and identifies
    systematic issues (e.g., 'driving segments with >30 km/h are frequently
    misclassified and should be train').

    Best for:
    - Initial quality audit after first processing real data
    - Finding systematic classifier weaknesses
    - Batch-correcting recurring misclassifications
    - Preparing training data for ML model improvement

    The response includes:
    - commute_summaries: overview of each reviewed commute
    - systematic_patterns: recurring misclassification types with counts
    - suggested_corrections: ready to pass to apply_label_corrections

    Args:
        n: Number of recent commutes to review (default 5)
        direction: Filter by commute direction (e.g. 'to_work', 'to_home')
    """
    service = ctx.request_context.lifespan_context.service
    result = service.review_recent_commutes(n=n, direction=direction)
    return json.dumps(result, indent=2)


@mcp.tool()
def apply_label_corrections(
    corrections: list[dict],
    ctx: Context,
    min_confidence: float = 0.7,
) -> str:
    """Apply suggested corrections from a review, filtered by confidence threshold.

    Takes the suggested_corrections output from review_commute_labels or
    review_recent_labels and applies them as durable label corrections.
    Only corrections with confidence >= min_confidence are applied.

    Workflow:
    1. Use review_commute_labels or review_recent_labels to get suggestions
    2. Inspect the suggestions (or adjust min_confidence)
    3. Call this tool with the suggestions to apply them

    For maximum accuracy, use a high min_confidence (0.8-0.9).
    For broader coverage, use a lower threshold (0.5-0.7).

    Args:
        corrections: List of correction dicts from a review's suggested_corrections
        min_confidence: Minimum confidence to apply (0.0-1.0, default 0.7)
    """
    service = ctx.request_context.lifespan_context.service
    result = service.apply_suggested_corrections(
        corrections=corrections,
        min_confidence=min_confidence,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def evaluate_classifier(ctx: Context) -> str:
    """Evaluate the automatic classifier against user-provided labels.

    Compares the ensemble classifier's output to human corrections to
    identify systematic misclassifications. Useful for understanding
    where the classifier struggles and whether more labels or config
    changes would help.
    """
    service = ctx.request_context.lifespan_context.service
    try:
        result = service.evaluate_classifier()
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS — reusable LLM interaction templates
# ══════════════════════════════════════════════════════════════════════════════


@mcp.prompt()
def analyze_commute(commute_id: str) -> str:
    """Analyze a specific commute in detail.

    Provides the full commute data and asks for insights about the journey:
    segment breakdown, unusual patterns, and optimization suggestions.
    """
    return (
        f"Please analyze commute '{commute_id}' in detail.\n\n"
        f"1. First, read the commute data from the commutes://{commute_id} resource\n"
        f"2. Examine each segment: mode, duration, speed, and distance\n"
        f"3. Look for:\n"
        f"   - Unusually long waiting or stationary segments\n"
        f"   - Segments that may be misclassified (check speed vs assigned mode)\n"
        f"   - Patterns that suggest schedule optimization opportunities\n"
        f"4. Compare this commute's total duration against the stats://overview averages\n"
        f"5. Provide specific, actionable recommendations"
    )


@mcp.prompt()
def optimize_departure() -> str:
    """Find the optimal departure time based on historical commute data.

    Analyzes patterns across all commutes to recommend the best departure
    windows for minimizing total commute time.
    """
    return (
        "Help me find the optimal departure time for my commute.\n\n"
        "1. Read the commute list from commutes://list\n"
        "2. Query the data to analyze duration by departure hour:\n"
        "   SELECT EXTRACT(HOUR FROM start_time) as hour, "
        "avg(duration_min) as avg_min, count(*) as trips "
        "FROM commute_data WHERE commute_id IS NOT NULL "
        "GROUP BY hour ORDER BY avg_min\n"
        "3. Also break down by day of week to find day-specific patterns\n"
        "4. Identify:\n"
        "   - The best departure window (lowest average duration)\n"
        "   - The worst times to avoid\n"
        "   - Day-of-week effects\n"
        "   - Which segment of the commute varies most (driving vs train vs waiting)\n"
        "5. Give me a concrete recommendation: 'Leave at X:XX on [days]'"
    )


@mcp.prompt()
def review_classifications() -> str:
    """Review segment classifications for accuracy.

    Guides the LLM through checking recent commutes for misclassified
    segments that should be corrected with labels.
    """
    return (
        "Help me review transport mode classifications for accuracy.\n\n"
        "1. Read the commute list from commutes://list\n"
        "2. For each of the 5 most recent commutes:\n"
        "   a. Read the segments from commutes://{commute_id}/segments\n"
        "   b. Check if any segment's speed profile doesn't match its mode:\n"
        "      - 'walking' should be 1-7 km/h\n"
        "      - 'driving' should be 7-80 km/h\n"
        "      - 'train' should be >30 km/h sustained\n"
        "      - 'waiting' should be near-stationary between different modes\n"
        "      - 'stationary' should be near-zero speed\n"
        "   c. Flag any suspicious segments\n"
        "3. For each flagged segment, suggest using add_segment_label to correct it\n"
        "4. After corrections, suggest rebuilding derived data"
    )


@mcp.prompt()
def weekly_report() -> str:
    """Generate a weekly commute report.

    Summarizes the past week's commuting patterns, highlights anomalies,
    and tracks progress toward schedule optimization goals.
    """
    return (
        "Generate a weekly commute report.\n\n"
        "1. Query for this week's commutes:\n"
        "   SELECT * FROM commute_data WHERE "
        "CAST(start_time AS DATE) >= CURRENT_DATE - INTERVAL '7 days'\n"
        "2. Summarize:\n"
        "   - Total commutes this week\n"
        "   - Average duration (compare to all-time average from stats://overview)\n"
        "   - Best and worst commute (with commute_ids)\n"
        "   - Time spent in each transport mode (walk/drive/train/waiting)\n"
        "3. Identify trends:\n"
        "   - Is my commute getting faster or slower?\n"
        "   - Which day was best/worst?\n"
        "   - Any new patterns emerging?\n"
        "4. Format as a clean, scannable report with key metrics up top"
    )
