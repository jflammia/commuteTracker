"""Minimal daily-at-hour scheduler. One asyncio task, no library dependency."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)


def next_run_at(now: datetime, *, hour_utc: int) -> datetime:
    candidate = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


async def run_daily(fn: Callable[[], object], *, hour_utc: int) -> None:
    while True:
        now = datetime.now(UTC)
        wait = (next_run_at(now, hour_utc=hour_utc) - now).total_seconds()
        await asyncio.sleep(wait)
        try:
            fn()
        except Exception:
            log.exception("daily job failed; will retry tomorrow")
