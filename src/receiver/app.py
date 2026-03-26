"""FastAPI receiver for OwnTracks HTTP POST payloads.

Accepts location data from OwnTracks iOS app, writes to local JSONL files,
and periodically syncs to S3-compatible storage.

CRITICAL: Always return 200. OwnTracks permanently discards data on 4xx responses.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from src.config import RAW_DATA_DIR, S3_BUCKET, S3_ENDPOINT_URL, S3_SYNC_INTERVAL_SECONDS
from src.storage.raw_store import append_record
from src.storage.s3_sync import S3Sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

s3_sync: S3Sync | None = None
sync_task: asyncio.Task | None = None


async def _periodic_s3_sync():
    """Background task: sync local JSONL to S3 every N seconds."""
    while True:
        await asyncio.sleep(S3_SYNC_INTERVAL_SECONDS)
        if s3_sync is not None:
            try:
                results = s3_sync.sync()
                uploaded = len(results["uploaded"])
                errors = len(results["errors"])
                if uploaded or errors:
                    logger.info(f"S3 sync: {uploaded} uploaded, {errors} errors")
            except Exception:
                logger.exception("S3 sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global s3_sync, sync_task

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Raw data dir: {RAW_DATA_DIR}")

    if S3_ENDPOINT_URL:
        s3_sync = S3Sync(
            local_dir=RAW_DATA_DIR,
            bucket=S3_BUCKET,
            endpoint_url=S3_ENDPOINT_URL,
        )
        if s3_sync.ensure_bucket():
            sync_task = asyncio.create_task(_periodic_s3_sync())
            logger.info(
                f"S3 sync enabled: {S3_ENDPOINT_URL}/{S3_BUCKET} "
                f"every {S3_SYNC_INTERVAL_SECONDS}s"
            )
        else:
            logger.warning("S3 bucket not available. Running without S3 sync.")
            s3_sync = None
    else:
        logger.info("S3 not configured. Running with local storage only.")

    yield

    if sync_task is not None:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass
        # Final sync on shutdown
        if s3_sync is not None:
            try:
                s3_sync.sync()
                logger.info("Final S3 sync complete.")
            except Exception:
                logger.exception("Final S3 sync failed")


app = FastAPI(title="Commute Tracker Receiver", lifespan=lifespan)


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

        path = append_record(RAW_DATA_DIR, payload)

        msg_type = payload.get("_type", "unknown")
        logger.info(
            f"Received {msg_type} from {x_limit_u}/{x_limit_d} -> {path.name}"
        )

    except Exception:
        # Log the error but ALWAYS return 200 to prevent OwnTracks data loss
        logger.exception(f"Error processing payload from {x_limit_u}/{x_limit_d}")

    return JSONResponse(content=[], status_code=200)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "raw_data_dir": str(RAW_DATA_DIR),
        "s3_enabled": s3_sync is not None,
    }
