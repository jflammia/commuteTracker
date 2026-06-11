import asyncio

import httpx
import pytest

from backend.sources import poller as poller_mod
from backend.sources.framework import SourceSpec
from backend.sources.poller import poll_source
from backend.storage.raw import RawStore


@pytest.mark.anyio
async def test_poll_source_fetches_then_sleeps(tmp_path, monkeypatch):
    calls = []
    sleeps = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"x" * len(calls))  # content changes each poll

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(poller_mod.asyncio, "sleep", fake_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    with pytest.raises(asyncio.CancelledError):
        await poll_source(client, spec, store)
    assert len(calls) == 3  # fetch-first, then sleep
    assert sleeps == [60.0, 60.0, 60.0]
    day_file = next((tmp_path / "raw" / "rt_path").glob("*.jsonl"))
    assert len(day_file.read_text().splitlines()) == 3


@pytest.mark.anyio
async def test_poll_source_survives_fetch_crash(tmp_path, monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async def boom(client, spec, store, state):
        raise RuntimeError("unexpected bug in fetch_once")

    monkeypatch.setattr(poller_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(poller_mod, "fetch_once", boom)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    with pytest.raises(asyncio.CancelledError):
        await poll_source(client, spec, RawStore(tmp_path))
    assert len(sleeps) == 2  # loop kept going despite the crash
