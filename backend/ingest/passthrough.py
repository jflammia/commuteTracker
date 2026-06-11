"""Fire-and-forget forward of ingested bodies to the legacy receiver.

Exists only for the migration period; failures are logged, never propagated.
"""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

# Compared case-insensitively: Starlette lower-cases incoming header names.
_FORWARD_HEADERS = ("x-limit-u", "x-limit-d", "content-type")


class Passthrough:
    def __init__(self, url: str | None, transport: httpx.AsyncBaseTransport | None = None):
        self._url = url
        self._client = (
            httpx.AsyncClient(transport=transport, timeout=5.0) if url is not None else None
        )
        self._inflight: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self._url is not None

    async def forward(self, body: bytes, headers: dict) -> None:
        if self._client is None:
            return
        fwd = {k: v for k, v in headers.items() if k.lower() in _FORWARD_HEADERS and v is not None}

        async def _send():
            try:
                await self._client.post(self._url, content=body, headers=fwd)
            except Exception as exc:
                log.warning("passthrough to legacy failed: %s", exc)

        task = asyncio.get_running_loop().create_task(_send())
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def aclose(self) -> None:
        # Wired into the app lifespan in Task 9 of the phase-1 plan.
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
