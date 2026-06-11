import base64
import json

import httpx
import pytest

from backend.config import Settings
from backend.sources.framework import SourceSpec, fetch_once, sources_from_settings
from backend.storage.raw import RawStore


def _settings_with(tmp_path, **kw):
    return Settings(
        data_dir=tmp_path,
        s3_bucket=None,
        s3_prefix="x",
        s3_region=None,
        passthrough_url=None,
        archive_hour_utc=6,
        **kw,
    )


def test_registry_includes_only_configured_sources(tmp_path):
    s = _settings_with(
        tmp_path, path_gtfs_url="https://e.com/p.zip", path_rt_url="https://e.com/rt"
    )
    specs = sources_from_settings(s)
    assert [(sp.name, sp.interval_s) for sp in specs] == [
        ("gtfs_path", 86400.0),
        ("rt_path", 60.0),
    ]


def test_registry_empty_when_nothing_configured(tmp_path):
    assert sources_from_settings(_settings_with(tmp_path)) == []


@pytest.mark.anyio
async def test_fetch_once_archives_body_before_anything(tmp_path):
    async def handler(request):
        return httpx.Response(200, content=b"zipbytes")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="gtfs_path", url="https://e.com/p.zip", interval_s=86400.0)
    state = {}
    ok = await fetch_once(client, spec, store, state)
    assert ok is True
    files = list((tmp_path / "raw" / "gtfs_path").glob("*.jsonl"))
    rec = json.loads(files[0].read_text().splitlines()[0])
    assert rec["payload"]["status"] == 200
    assert base64.b64decode(rec["payload"]["b64"]) == b"zipbytes"
    assert len(rec["payload"]["sha256"]) == 64


@pytest.mark.anyio
async def test_fetch_once_unchanged_content_archives_marker_not_body(tmp_path):
    async def handler(request):
        return httpx.Response(200, content=b"zipbytes")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="gtfs_path", url="https://e.com/p.zip", interval_s=86400.0)
    state = {}
    await fetch_once(client, spec, store, state)
    await fetch_once(client, spec, store, state)
    lines = (tmp_path / "raw" / "gtfs_path").glob("*.jsonl").__next__().read_text().splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])["payload"]
    assert second["unchanged"] is True
    assert "b64" not in second


@pytest.mark.anyio
async def test_fetch_once_error_archives_error_event(tmp_path):
    async def handler(request):
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    ok = await fetch_once(client, spec, store, {})
    assert ok is False
    rec = json.loads(
        (tmp_path / "raw" / "rt_path").glob("*.jsonl").__next__().read_text().splitlines()[0]
    )
    assert rec["payload"]["status"] is None
    assert "down" in rec["payload"]["error"]


@pytest.mark.anyio
async def test_fetch_once_non_200_archived_and_reported_false(tmp_path):
    async def handler(request):
        return httpx.Response(503, content=b"maintenance")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = RawStore(tmp_path)
    spec = SourceSpec(name="rt_path", url="https://e.com/rt", interval_s=60.0)
    ok = await fetch_once(client, spec, store, {})
    assert ok is False
    rec = json.loads(
        (tmp_path / "raw" / "rt_path").glob("*.jsonl").__next__().read_text().splitlines()[0]
    )
    assert rec["payload"]["status"] == 503
