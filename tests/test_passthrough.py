"""Tests for OwnTracks Recorder passthrough."""

import json
from unittest.mock import MagicMock, patch

from src.receiver.passthrough import _do_forward


def test_forward_success():
    with patch("src.receiver.passthrough.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        payload = json.dumps({"_type": "location", "lat": 40.75, "lon": -74.0}).encode()
        _do_forward("http://recorder:8083/pub", payload, "testuser", "phone")

        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        assert "u=testuser" in request.full_url
        assert "d=phone" in request.full_url


def test_forward_handles_network_error():
    """Passthrough should not raise on network errors."""
    from urllib.error import URLError

    with patch("src.receiver.passthrough.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = URLError("Connection refused")

        payload = json.dumps({"_type": "location"}).encode()
        # Should not raise
        _do_forward("http://recorder:8083/pub", payload, "testuser", "phone")


def test_forward_handles_http_error():
    """Passthrough should not raise on HTTP errors."""
    with patch("src.receiver.passthrough.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        payload = json.dumps({"_type": "location"}).encode()
        # Should not raise
        _do_forward("http://recorder:8083/pub", payload, "testuser", "phone")
