"""FastAPI receiver for OwnTracks HTTP POST payloads.

Accepts location data from OwnTracks iOS app, writes to database (SQLite/PostgreSQL),
periodically exports to S3 as JSONL, and optionally forwards to OwnTracks Recorder.

Also serves:
- REST API at /api/v1/* for programmatic access
- MCP server at /mcp for LLM integration (Streamable HTTP)

CRITICAL: Always return 200 on /pub. OwnTracks permanently discards data on 4xx responses.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from src.config import (
    DATABASE_URL,
    LOCAL_RETENTION_DAYS,
    RECORDER_URL,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_SYNC_INTERVAL_SECONDS,
)
from src.receiver.passthrough import forward_to_recorder
from src.storage.database import Database
from src.storage.s3_sync import S3Sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

db: Database | None = None
s3_sync: S3Sync | None = None
sync_task: asyncio.Task | None = None


async def _periodic_s3_sync():
    """Background task: export from DB to S3 every N seconds, then prune."""
    while True:
        await asyncio.sleep(S3_SYNC_INTERVAL_SECONDS)
        if s3_sync is not None and db is not None:
            try:
                results = s3_sync.sync_from_db(db, retention_days=LOCAL_RETENTION_DAYS)
                uploaded = len(results["uploaded"])
                errors = len(results["errors"])
                pruned = results["pruned"]
                if uploaded or errors or pruned:
                    logger.info(
                        f"S3 sync: {results['synced']} records exported, "
                        f"{uploaded} files uploaded, {pruned} pruned, {errors} errors"
                    )
            except Exception:
                logger.exception("S3 sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, s3_sync, sync_task

    # Initialize database
    # Ensure parent directory exists for SQLite
    if DATABASE_URL.startswith("sqlite"):
        db_path = DATABASE_URL.replace("sqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    db = Database(DATABASE_URL)
    db.create_tables()
    logger.info(
        f"Database ready: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}"
    )

    # Initialize S3 sync (optional)
    if S3_ENDPOINT_URL:
        s3_sync = S3Sync(
            bucket=S3_BUCKET,
            endpoint_url=S3_ENDPOINT_URL,
        )
        if s3_sync.ensure_bucket():
            sync_task = asyncio.create_task(_periodic_s3_sync())
            prune_status = (
                f"pruning after {LOCAL_RETENTION_DAYS}d"
                if LOCAL_RETENTION_DAYS > 0
                else "no pruning"
            )
            logger.info(
                f"S3 sync enabled: {S3_ENDPOINT_URL}/{S3_BUCKET} "
                f"every {S3_SYNC_INTERVAL_SECONDS}s ({prune_status})"
            )
        else:
            logger.warning("S3 bucket not available. Running without S3 sync.")
            s3_sync = None
    else:
        logger.info("S3 not configured. Local database is the only data store. Pruning disabled.")

    # Log Recorder passthrough status
    if RECORDER_URL:
        logger.info(f"Recorder passthrough enabled: {RECORDER_URL}")
    else:
        logger.info("Recorder passthrough disabled (RECORDER_URL not set)")

    # Initialize REST API service
    from src.api.routes import router as api_router, set_service
    from src.api.service import CommuteService

    service = CommuteService(db=db)
    set_service(service)
    app.include_router(api_router)
    logger.info("REST API mounted at /api/v1")

    # Mount MCP server
    mcp_session_mgr = None
    try:
        from src.mcp_server import mcp as mcp_server, set_service as set_mcp_service

        set_mcp_service(service)
        mcp_app = mcp_server.streamable_http_app()
        app.mount("/mcp", mcp_app)
        # The MCP session manager must be started explicitly because FastAPI
        # does not propagate lifespan events to mounted sub-applications.
        mcp_session_mgr = mcp_server.session_manager
        logger.info("MCP server mounted at /mcp")
    except ImportError:
        logger.warning("MCP server not available (mcp package not installed)")
    except Exception:
        logger.exception("Failed to mount MCP server")

    if mcp_session_mgr is not None:
        async with mcp_session_mgr.run():
            yield
    else:
        yield

    # Shutdown: final S3 sync
    if sync_task is not None:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        if s3_sync is not None and db is not None:
            try:
                s3_sync.sync_from_db(db, retention_days=0)  # Don't prune on shutdown
                logger.info("Final S3 sync complete.")
            except Exception:
                logger.exception("Final S3 sync failed")


app = FastAPI(
    title="Commute Tracker",
    version=pkg_version("commute-tracker"),
    description=(
        "GPS-based commute tracking system. Collects location data from OwnTracks, "
        "segments commutes into transport modes (walk, drive, train, waiting), and "
        "provides analytics for schedule optimization.\n\n"
        "## Interfaces\n\n"
        "- **OwnTracks receiver** — `POST /pub` (always returns 200)\n"
        "- **REST API** — `/api/v1/*` (this documentation)\n"
        "- **MCP server** — `/mcp` (Streamable HTTP for LLM integration)\n"
        "- **Streamlit dashboard** — port 8501 (consumes this API)\n\n"
        "## Key concepts\n\n"
        "- **Commute**: A detected trip between home and work (or vice versa)\n"
        "- **Segment**: A leg of a commute in a single transport mode\n"
        "- **Label**: A user correction to a segment's auto-classified mode\n"
        "- **Derived data**: Parquet files rebuilt from raw GPS records\n"
    ),
    lifespan=lifespan,
)


@app.post("/pub")
async def receive_location(
    request: Request,
    x_limit_u: str = Header(default="unknown", alias="X-Limit-U"),
    x_limit_d: str = Header(default="unknown", alias="X-Limit-D"),
):
    """Receive OwnTracks HTTP POST. Always returns 200 with empty JSON array.

    OwnTracks permanently discards data if it receives a 4xx response,
    so we catch all errors and still return 200.
    """
    try:
        body = await request.body()

        # OwnTracks sends zero-length payload when a friend is deleted; ignore it
        if not body:
            return JSONResponse(content=[], status_code=200)

        payload = await request.json()

        # Add server-side metadata
        payload["received_at"] = datetime.now(timezone.utc).isoformat()
        payload["_receiver_user"] = x_limit_u
        payload["_receiver_device"] = x_limit_d

        # Write to database
        record_id = db.insert_record(payload, user=x_limit_u, device=x_limit_d)

        msg_type = payload.get("_type", "unknown")
        logger.info(f"Received {msg_type} from {x_limit_u}/{x_limit_d} (id={record_id})")

        # Forward to Recorder (fire-and-forget)
        if RECORDER_URL:
            forward_to_recorder(RECORDER_URL, body, x_limit_u, x_limit_d)

    except Exception:
        # Log the error but ALWAYS return 200 to prevent OwnTracks data loss
        logger.exception(f"Error processing payload from {x_limit_u}/{x_limit_d}")

    return JSONResponse(content=[], status_code=200)


@app.get("/health")
async def health():
    """Health check endpoint."""
    total = db.count_records() if db else 0
    unsynced = db.count_unsynced() if db else 0
    return {
        "status": "ok",
        "database": DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL,
        "s3_enabled": s3_sync is not None,
        "recorder_enabled": bool(RECORDER_URL),
        "total_records": total,
        "unsynced_records": unsynced,
    }
