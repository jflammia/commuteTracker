"""Pluggable external data sources, archive-first.

Every fetch is recorded as a raw event BEFORE any parsing happens — external
observations are primitive data (you can't re-fetch the past). Adding a
source = one SourceSpec + one config URL. The response body travels base64
inside the event payload through the existing JSONL→Parquet→S3 pipeline.
"""

import base64
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from backend.config import Settings
from backend.storage.raw import RawStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSpec:
    name: str  # raw stream name
    url: str
    interval_s: float


def sources_from_settings(settings: Settings) -> list[SourceSpec]:
    """A source exists iff its URL is configured. NJT sources use njt.py, not this registry."""
    table = (
        ("gtfs_path", settings.path_gtfs_url, settings.gtfs_refresh_interval_s),
        ("rt_path", settings.path_rt_url, settings.source_poll_interval_s),
    )
    return [
        SourceSpec(name=name, url=url, interval_s=interval) for name, url, interval in table if url
    ]


async def fetch_once(
    client: httpx.AsyncClient, spec: SourceSpec, store: RawStore, state: dict
) -> bool:
    """Fetch the source once and archive the outcome. Returns True on HTTP 200.

    `state` holds the last-archived body sha256 per source name (in-memory;
    a restart re-archives one full copy, which is harmless).
    """
    received_at = datetime.now(UTC).isoformat()
    try:
        resp = await client.get(spec.url, timeout=30.0)
        digest = hashlib.sha256(resp.content).hexdigest()
        if resp.status_code == 200 and state.get(spec.name) == digest:
            payload = {
                "url": spec.url,
                "status": resp.status_code,
                "sha256": digest,
                "unchanged": True,
            }
        else:
            payload = {
                "url": spec.url,
                "status": resp.status_code,
                "sha256": digest,
                "b64": base64.b64encode(resp.content).decode("ascii"),
            }
            if resp.status_code == 200:
                state[spec.name] = digest
        ok = resp.status_code == 200
    except Exception as exc:
        payload = {"url": spec.url, "status": None, "error": str(exc)}
        ok = False
    store.append(spec.name, {"received_at": received_at, "payload": payload})
    return ok
