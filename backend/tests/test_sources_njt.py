import json

import httpx
import pytest

from backend.sources.njt import NjtSpec, NjtTokenManager, fetch_njt_once, njt_specs_from_settings
from backend.storage.raw import RawStore

API = "https://test-njt.example/api/GTFSRT"


def _transport(state):
    """Replicates raildata.njtransit.com behavior observed 2026-06-11."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode(errors="replace")
        if str(request.url).endswith("/getToken"):
            state["token_calls"] = state.get("token_calls", 0) + 1
            if "gooduser" in body:
                return httpx.Response(200, json={"UserToken": f"tok{state['token_calls']}"})
            return httpx.Response(500, json={"errorMessage": "Missing user account."})
        # data endpoint: needs a CURRENT token
        current = f"tok{state.get('token_calls', 0)}"
        if current not in body or state.get("expire_all"):
            return httpx.Response(500, json={"errorMessage": "Invalid token."})
        return httpx.Response(200, content=state.get("body", b"protobytes"))

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_token_exchange_then_fetch(tmp_path):
    state = {}
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "gooduser", "pw", tmp_path)
    spec = NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates", interval_s=60.0)
    store = RawStore(tmp_path)
    ok = await fetch_njt_once(client, mgr, spec, store, {})
    assert ok is True
    assert state["token_calls"] == 1
    assert (tmp_path / "njt_token.txt").read_text() == "tok1"
    rec = json.loads(
        next((tmp_path / "raw" / "rt_njt_trips").glob("*.jsonl")).read_text().splitlines()[0]
    )
    assert rec["payload"]["status"] == 200


@pytest.mark.anyio
async def test_persisted_token_skips_exchange(tmp_path):
    (tmp_path / "njt_token.txt").write_text("tok1")
    state = {"token_calls": 1}  # pretend tok1 was issued earlier
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "gooduser", "pw", tmp_path)
    spec = NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates", interval_s=60.0)
    ok = await fetch_njt_once(client, mgr, spec, RawStore(tmp_path), {})
    assert ok is True
    assert state["token_calls"] == 1  # no new exchange


@pytest.mark.anyio
async def test_invalid_token_triggers_one_refresh(tmp_path):
    (tmp_path / "njt_token.txt").write_text("stale")
    state = {"token_calls": 1}  # current valid token is tok1, ours is "stale"
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "gooduser", "pw", tmp_path)
    spec = NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates", interval_s=60.0)
    ok = await fetch_njt_once(client, mgr, spec, RawStore(tmp_path), {})
    assert ok is True
    assert state["token_calls"] == 2  # exactly one refresh
    assert (tmp_path / "njt_token.txt").read_text() == "tok2"


@pytest.mark.anyio
async def test_unprovisioned_account_archives_error(tmp_path):
    state = {}
    client = httpx.AsyncClient(transport=_transport(state))
    mgr = NjtTokenManager(API, "newuser", "pw", tmp_path)  # not provisioned
    spec = NjtSpec(name="gtfs_njt", endpoint="getGTFS", interval_s=86400.0)
    ok = await fetch_njt_once(client, mgr, spec, RawStore(tmp_path), {})
    assert ok is False
    rec = json.loads(
        next((tmp_path / "raw" / "gtfs_njt").glob("*.jsonl")).read_text().splitlines()[0]
    )
    assert rec["payload"]["status"] is None
    assert "token" in rec["payload"]["error"]


@pytest.mark.anyio
async def test_concurrent_cold_start_coalesces_to_one_exchange(tmp_path):
    import asyncio as _asyncio

    state = {}

    async def slow_handler(request):
        if str(request.url).endswith("/getToken"):
            state["token_calls"] = state.get("token_calls", 0) + 1
            await _asyncio.sleep(0.05)  # widen the race window
            return httpx.Response(200, json={"UserToken": f"tok{state['token_calls']}"})
        current = f"tok{state.get('token_calls', 0)}"
        if current not in request.content.decode(errors="replace"):
            return httpx.Response(500, json={"errorMessage": "Invalid token."})
        return httpx.Response(200, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
    mgr = NjtTokenManager(API, "gooduser", "pw", tmp_path)
    specs = [
        NjtSpec(name="gtfs_njt", endpoint="getGTFS", interval_s=86400.0),
        NjtSpec(name="rt_njt_trips", endpoint="getTripUpdates", interval_s=60.0),
        NjtSpec(name="rt_njt_alerts", endpoint="getAlerts", interval_s=60.0),
    ]
    store = RawStore(tmp_path)
    results = await _asyncio.gather(*(fetch_njt_once(client, mgr, s, store, {}) for s in specs))
    assert all(results)  # all three fetched ok
    assert state["token_calls"] == 1  # exactly ONE getToken despite 3 concurrent cold-start callers


def test_specs_gated_on_credentials(tmp_path):
    import dataclasses

    from backend.config import Settings

    base = Settings(
        data_dir=tmp_path,
        s3_bucket=None,
        s3_prefix="x",
        s3_region=None,
        passthrough_url=None,
        archive_hour_utc=6,
    )
    assert njt_specs_from_settings(base) == []
    with_creds = dataclasses.replace(base, njt_username="u", njt_password="p")
    assert [s.name for s in njt_specs_from_settings(with_creds)] == [
        "gtfs_njt",
        "rt_njt_trips",
        "rt_njt_alerts",
    ]
