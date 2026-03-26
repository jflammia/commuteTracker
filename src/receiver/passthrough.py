"""Optional fire-and-forget passthrough to OwnTracks Recorder.

When RECORDER_URL is configured, forwards every incoming OwnTracks payload
to the Recorder's /pub endpoint. This gives users the Recorder's web UI
and reverse geocoding without making it the primary data store.

Design:
- Fire-and-forget: if the Recorder is down, we don't care
- Our data is already persisted in the database before forwarding
- Non-blocking: runs in a background thread to avoid slowing the receiver
- Logs warnings on failure but never raises
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)


def forward_to_recorder(recorder_url: str, payload: bytes, user: str, device: str):
    """Forward raw payload bytes to the OwnTracks Recorder, fire-and-forget."""
    _executor.submit(_do_forward, recorder_url, payload, user, device)


def _do_forward(recorder_url: str, payload: bytes, user: str, device: str):
    """Actual HTTP POST to Recorder. Runs in background thread."""
    try:
        url = f"{recorder_url}?u={user}&d={device}"
        req = Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            if resp.status >= 400:
                logger.warning(f"Recorder returned {resp.status} for {user}/{device}")
    except URLError as e:
        logger.warning(f"Recorder passthrough failed ({user}/{device}): {e}")
    except Exception:
        logger.exception(f"Recorder passthrough error ({user}/{device})")
