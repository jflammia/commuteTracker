import asyncio

import httpx
import pytest

from backend.ingest.passthrough import Passthrough


def test_disabled_when_no_url():
    pt = Passthrough(None)
    assert pt.enabled is False


@pytest.mark.anyio
async def test_forwards_body_and_headers():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        seen["u"] = request.headers.get("X-Limit-U")
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    pt = Passthrough("http://legacy:8080/pub", transport=transport)
    await pt.forward(b'{"_type":"location"}', {"x-limit-u": "justin"})
    await asyncio.sleep(0)  # let the fire-and-forget task run
    assert seen["url"] == "http://legacy:8080/pub"
    assert seen["body"] == b'{"_type":"location"}'
    assert seen["u"] == "justin"


@pytest.mark.anyio
async def test_legacy_failure_never_raises():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("legacy down")

    pt = Passthrough("http://legacy:8080/pub", transport=httpx.MockTransport(handler))
    await pt.forward(b"{}", {})  # must not raise
    await asyncio.sleep(0)
