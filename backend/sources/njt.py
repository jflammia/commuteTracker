"""NJ Transit RailData GTFS-RT source: token-exchange auth.

POST getToken (multipart username/password) -> {"UserToken": ...}; data
endpoints take a multipart `token` field. Tokens are daily-rate-limited, so
the manager caches in memory AND persists to <data_dir>/njt_token.txt; it
re-exchanges only when an endpoint reports a token problem."""

import asyncio
import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.storage.raw import RawStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NjtSpec:
    name: str  # raw stream name, e.g. "gtfs_njt"
    endpoint: str  # e.g. "getGTFS"
    interval_s: float


class NjtTokenManager:
    def __init__(self, api_base: str, username: str, password: str, data_dir: Path):
        self._api_base = api_base.rstrip("/")
        self._username = username
        self._password = password
        self._path = data_dir / "njt_token.txt"
        self._token: str | None = (
            self._path.read_text().strip() if self._path.exists() else None
        ) or None
        self._lock = asyncio.Lock()

    async def token(self, client: httpx.AsyncClient) -> str | None:
        if self._token:
            return self._token
        return await self.refresh(client, expected=None)

    async def refresh(
        self, client: httpx.AsyncClient, expected: str | None = "__unset__"
    ) -> str | None:  # type: ignore[assignment]
        """Exchange credentials for a fresh token, coalescing concurrent callers.

        `expected` is the token the caller believed was current (None on cold
        start; the stale token string when retrying after a token error). Under
        the lock we re-check: if another coroutine already refreshed past
        `expected`, return the new token instead of burning another exchange.
        """
        async with self._lock:
            if expected != "__unset__" and self._token != expected and self._token:
                return self._token  # another coroutine already refreshed
            try:
                resp = await client.post(
                    f"{self._api_base}/getToken",
                    files={"username": (None, self._username), "password": (None, self._password)},
                    timeout=30.0,
                )
                data = resp.json()
                tok = data.get("UserToken")
                if tok:
                    self._token = tok
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    self._path.write_text(tok)
                    os.chmod(self._path, 0o600)
                    return tok
                log.warning("njt getToken failed: %s", data.get("errorMessage"))
            except Exception:
                log.exception("njt getToken request failed")
        return None


def njt_specs_from_settings(settings) -> list[NjtSpec]:
    if not (settings.njt_username and settings.njt_password):
        return []
    return [
        NjtSpec(name="gtfs_njt", endpoint="getGTFS", interval_s=settings.gtfs_refresh_interval_s),
        NjtSpec(
            name="rt_njt_trips",
            endpoint="getTripUpdates",
            interval_s=settings.source_poll_interval_s,
        ),
        NjtSpec(
            name="rt_njt_alerts", endpoint="getAlerts", interval_s=settings.source_poll_interval_s
        ),
    ]


def _is_token_error(resp: httpx.Response) -> bool:
    if resp.status_code != 500:
        return False
    try:
        msg = (resp.json().get("errorMessage") or "").lower()
    except Exception:
        return False
    return "token" in msg


async def fetch_njt_once(
    client: httpx.AsyncClient,
    manager: NjtTokenManager,
    spec: NjtSpec,
    store: RawStore,
    state: dict,
) -> bool:
    received_at = datetime.now(UTC).isoformat()
    url = f"{manager._api_base}/{spec.endpoint}"
    try:
        token = await manager.token(client)
        if token is None:
            payload = {"url": url, "status": None, "error": "no njt token available"}
            store.append(spec.name, {"received_at": received_at, "payload": payload})
            return False
        resp = await client.post(url, files={"token": (None, token)}, timeout=60.0)
        if _is_token_error(resp):
            token = await manager.refresh(client, expected=token)
            if token is not None:
                resp = await client.post(url, files={"token": (None, token)}, timeout=60.0)
        digest = hashlib.sha256(resp.content).hexdigest()
        if resp.status_code == 200 and state.get(spec.name) == digest:
            payload = {"url": url, "status": resp.status_code, "sha256": digest, "unchanged": True}
        else:
            payload = {
                "url": url,
                "status": resp.status_code,
                "sha256": digest,
                "b64": base64.b64encode(resp.content).decode("ascii"),
            }
            if resp.status_code == 200:
                state[spec.name] = digest
        ok = resp.status_code == 200
    except Exception as exc:
        payload = {"url": url, "status": None, "error": str(exc)}
        ok = False
    store.append(spec.name, {"received_at": received_at, "payload": payload})
    return ok
