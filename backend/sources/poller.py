"""One asyncio polling task per enabled source. Fetch first, then sleep, so a
static source (interval 24 h) is fetched immediately at startup."""

import asyncio
import logging

import httpx

from backend.sources.framework import SourceSpec, fetch_once
from backend.storage.raw import RawStore

log = logging.getLogger(__name__)


async def poll_source(client: httpx.AsyncClient, spec: SourceSpec, store: RawStore) -> None:
    state: dict = {}
    while True:
        try:
            await fetch_once(client, spec, store, state)
        except Exception:
            log.exception("source %s poll iteration failed", spec.name)
        await asyncio.sleep(spec.interval_s)


def start_pollers(
    client: httpx.AsyncClient, specs: list[SourceSpec], store: RawStore
) -> list[asyncio.Task]:
    tasks = [asyncio.create_task(poll_source(client, spec, store)) for spec in specs]
    if tasks:
        log.info("started %d source pollers: %s", len(tasks), [s.name for s in specs])
    return tasks
