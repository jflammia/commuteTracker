import asyncio
from datetime import UTC, datetime

import pytest

from backend.jobs.daily import next_run_at, run_daily


def test_next_run_today_if_before_hour():
    now = datetime(2026, 6, 10, 4, 30, tzinfo=UTC)
    assert next_run_at(now, hour_utc=6) == datetime(2026, 6, 10, 6, 0, tzinfo=UTC)


def test_next_run_tomorrow_if_past_hour():
    now = datetime(2026, 6, 10, 6, 0, 1, tzinfo=UTC)
    assert next_run_at(now, hour_utc=6) == datetime(2026, 6, 11, 6, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_run_daily_invokes_fn_and_survives_failure(monkeypatch):
    calls = []
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError  # stop the loop after 3 iterations

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first run fails")

    # Patch asyncio.sleep at the module level to avoid touching asyncio internals
    # used by asyncio.to_thread (run_in_executor, not sleep-based).
    import backend.jobs.daily as daily_mod

    monkeypatch.setattr(daily_mod.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await run_daily(flaky, hour_utc=6)
    assert len(calls) == 2  # ran twice despite first failure
    assert len(sleeps) == 3
