"""Regression tests for audit fixes.

Covers:
1. MCP server mounting and initialization
2. Pipeline date filters using GPS tst (not received_at)
3. Pipeline robustness with non-location and malformed records
4. SQL injection prevention in derived_store (parameterized queries)
5. SQL sandboxing in query_derived (SELECT-only)
6. Batch segments endpoint
7. Bulk labels accepting both formats
8. Vectorized day-of-week replace (map_elements removed)
9. MCP server works behind reverse proxy (no Host header rejection)
10. .mcp.json uses correct transport type for Claude Code (#5)
11. Container data paths resolve to writable volume (#6)
"""

import time
from unittest.mock import patch

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import router, set_service
from src.api.service import CommuteService
from src.processing.pipeline import process_from_db, process_locations
from src.storage.database import Database
from src.storage.derived_store import DerivedStore

HOME = (40.75, -74.00)
WORK = (40.85, -73.95)


# ── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    database = Database(db_url)
    database.create_tables()
    return database


@pytest.fixture
def derived_dir(tmp_path):
    d = tmp_path / "derived"
    d.mkdir()
    return d


@pytest.fixture
def service(db, derived_dir):
    return CommuteService(db=db, derived_dir=derived_dir)


@pytest.fixture
def client(service):
    set_service(service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _insert_location(db, lat, lon, tst, user="test", device="phone", msg_type="location"):
    payload = {
        "_type": msg_type,
        "lat": lat,
        "lon": lon,
        "tst": tst,
        "acc": 10,
        "alt": 50,
        "batt": 85,
        "vel": 0,
        "tid": "te",
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(tst)),
    }
    return db.insert_record(payload, user=user, device=device)


def _pipeline_config():
    return patch.multiple(
        "src.processing.pipeline",
        HOME_LAT=HOME[0],
        HOME_LON=HOME[1],
        HOME_RADIUS_M=200.0,
        WORK_LAT=WORK[0],
        WORK_LON=WORK[1],
        WORK_RADIUS_M=200.0,
    )


def _seed_commute(db, base_tst=1711440000):
    """Insert a minimal commute: home points -> transit -> work points."""
    # At home
    for i in range(3):
        _insert_location(db, HOME[0], HOME[1], base_tst + i * 10)
    # Transit
    for i in range(1, 11):
        frac = i / 10
        lat = HOME[0] + (WORK[0] - HOME[0]) * frac
        lon = HOME[1] + (WORK[1] - HOME[1]) * frac
        _insert_location(db, lat, lon, base_tst + 30 + i * 10)
    # At work
    for i in range(3):
        _insert_location(db, WORK[0], WORK[1], base_tst + 140 + i * 10)


def _rebuild_all(db, derived_dir):
    """Run the pipeline and return results."""
    with _pipeline_config():
        return process_from_db(db, output_dir=derived_dir)


# ── Fix 1: MCP server mounting ──────────────────────────────────────────────


def test_mcp_streamable_http_path():
    """MCP server should configure streamable_http_path='/' to avoid /mcp/mcp."""
    from src.mcp_server import mcp

    assert mcp.settings.streamable_http_path == "/"


def test_mcp_session_manager_accessible():
    """MCP server should expose session_manager for manual lifespan management."""
    from src.mcp_server import mcp

    # streamable_http_app() creates the session manager
    mcp.streamable_http_app()
    assert mcp.session_manager is not None


def test_mcp_host_not_localhost():
    """MCP host must not be localhost to avoid DNS rebinding protection blocking proxies.

    When host is 127.0.0.1/localhost, the MCP SDK auto-enables strict Host header
    validation that rejects requests from reverse proxies. Setting host to 0.0.0.0
    (or any non-localhost value) disables this, allowing proxy Host headers through.
    See: https://github.com/jflammia/commuteTracker/issues/3
    """
    from src.mcp_server import mcp

    assert mcp.settings.host not in ("127.0.0.1", "localhost", "::1")


def test_mcp_dns_rebinding_protection_disabled():
    """DNS rebinding protection should not be auto-enabled for non-localhost host.

    The MCP SDK only auto-enables it when host is localhost. Since we set
    host=RECEIVER_HOST (0.0.0.0), transport_security should either be None
    or have enable_dns_rebinding_protection=False.
    """
    from src.mcp_server import mcp

    security = mcp.settings.transport_security
    if security is not None:
        assert not security.enable_dns_rebinding_protection


def test_mcp_accepts_proxy_host_header():
    """MCP server should accept requests with non-localhost Host headers.

    This simulates what happens behind a reverse proxy where the Host header
    is the proxy's external hostname, not localhost.
    """
    from src.mcp_server import mcp

    # The app should either have no security middleware or one that allows any host
    security = mcp.settings.transport_security
    if security is not None and security.enable_dns_rebinding_protection:
        # If protection is enabled, proxy hostnames must be in the allowed list
        assert any(h in security.allowed_hosts for h in ["*", "0.0.0.0:*"]), (
            f"Proxy hosts not allowed: {security.allowed_hosts}"
        )


# ── Fix 2: Date filters use GPS tst ─────────────────────────────────────────


def test_pipeline_date_filter_uses_gps_tst(db, derived_dir):
    """Date filters should match GPS timestamp, not server received_at."""
    # Insert records with GPS tst on 2024-03-26 (1711411200 = 2024-03-26 00:00 UTC)
    march26_tst = 1711411200
    for i in range(5):
        _insert_location(db, 40.75 + i * 0.001, -74.00, march26_tst + i * 60)

    # Insert records with GPS tst on 2024-03-27
    march27_tst = 1711497600
    for i in range(5):
        _insert_location(db, 40.76 + i * 0.001, -74.00, march27_tst + i * 60)

    # Filter to March 26 only — should find exactly 5 records
    with _pipeline_config():
        results = process_from_db(
            db, output_dir=derived_dir, filters={"since": "2024-03-26", "until": "2024-03-26"}
        )
    assert results["total_records"] == 5


def test_pipeline_date_filter_excludes_out_of_range(db, derived_dir):
    """Records outside the date range should be excluded."""
    tst = 1711411200  # 2024-03-26
    for i in range(3):
        _insert_location(db, 40.75, -74.00, tst + i * 60)

    # Filter to a different date — should find nothing
    with _pipeline_config():
        results = process_from_db(
            db, output_dir=derived_dir, filters={"since": "2025-01-01", "until": "2025-01-31"}
        )
    assert results["total_records"] == 0


# ── Fix 3 & 8: Pipeline robustness ──────────────────────────────────────────


def test_pipeline_filters_non_location_records(db, derived_dir):
    """Non-location message types should be filtered out at the DB level."""
    tst = 1711440000
    # Insert 3 locations and 2 non-locations
    _insert_location(db, 40.75, -74.00, tst, msg_type="location")
    _insert_location(db, 40.75, -74.00, tst + 10, msg_type="transition")
    _insert_location(db, 40.76, -74.00, tst + 20, msg_type="location")
    _insert_location(db, 40.76, -74.00, tst + 30, msg_type="card")
    _insert_location(db, 40.77, -74.00, tst + 40, msg_type="location")

    with _pipeline_config():
        results = process_from_db(db, output_dir=derived_dir)
    # Only the 3 location records should be processed
    assert results["total_records"] == 3


def test_pipeline_handles_null_required_fields(db, derived_dir):
    """Records with null lat/lon/tst should be dropped without crashing."""
    tst = 1711440000
    # Insert a valid record
    _insert_location(db, 40.75, -74.00, tst)
    # Insert a record with null lat (malformed)
    payload = {
        "_type": "location",
        "lat": None,
        "lon": -74.00,
        "tst": tst + 10,
        "received_at": "2024-03-26T00:00:10Z",
    }
    db.insert_record(payload, user="test", device="phone")

    with _pipeline_config():
        results = process_from_db(db, output_dir=derived_dir)
    # Should process 1 valid record, not crash on the null one
    assert results["total_records"] == 1


def test_pipeline_rebuild_no_filters_does_not_crash(db, derived_dir):
    """Rebuild with no filters (the scenario that caused the 500 error)."""
    tst = 1711440000
    # Mix of message types
    _insert_location(db, 40.75, -74.00, tst, msg_type="location")
    _insert_location(db, 40.75, -74.00, tst + 10, msg_type="transition")
    _insert_location(db, 40.76, -74.00, tst + 20, msg_type="location")

    # Should not raise — this was the 500 error scenario
    with _pipeline_config():
        results = process_from_db(db, output_dir=derived_dir)
    assert results["total_records"] == 2


def test_process_locations_filters_type_column():
    """process_locations should filter to _type=location when column exists."""
    df = pl.DataFrame(
        [
            {"_type": "location", "lat": 40.75, "lon": -74.00, "tst": 1000},
            {"_type": "transition", "lat": 40.75, "lon": -74.00, "tst": 1010},
            {"_type": "location", "lat": 40.76, "lon": -74.00, "tst": 1020},
        ]
    )
    with _pipeline_config():
        result = process_locations(df)
    assert len(result) == 2


# ── Fix 4: SQL injection prevention ─────────────────────────────────────────


def test_derived_store_parameterized_segments(db, derived_dir):
    """get_segments should use parameterized queries, not f-string interpolation."""
    _seed_commute(db)
    _rebuild_all(db, derived_dir)

    store = DerivedStore(derived_dir)
    # A normal query should work
    commutes = store.get_commutes()
    if not commutes.is_empty():
        cid = commutes["commute_id"][0]
        segments = store.get_segments(cid)
        assert not segments.is_empty()

    # An injection attempt should be safely handled (treated as literal string)
    evil = "'; DROP TABLE commute_data; --"
    result = store.get_segments(evil)
    assert result.is_empty()


def test_derived_store_parameterized_points(db, derived_dir):
    """get_commute_points should use parameterized queries."""
    _seed_commute(db)
    _rebuild_all(db, derived_dir)

    store = DerivedStore(derived_dir)
    evil = "' OR '1'='1"
    result = store.get_commute_points(evil)
    assert result.is_empty()


def test_derived_store_parameterized_daily(db, derived_dir):
    """get_daily_summary should use parameterized queries."""
    _seed_commute(db)
    _rebuild_all(db, derived_dir)

    store = DerivedStore(derived_dir)
    # Valid date should work
    result = store.get_daily_summary("2024-03-26")
    assert not result.is_empty()

    # Non-existent date should return empty (proves parameter is used, not concatenated)
    result = store.get_daily_summary("1999-01-01")
    assert result.is_empty()


# ── Fix 5: SQL sandboxing ───────────────────────────────────────────────────


def test_query_derived_blocks_non_select(service):
    """Only SELECT queries should be allowed via query_derived."""
    with pytest.raises(ValueError, match="Only SELECT"):
        service.query_derived("CREATE TABLE evil (id int)")


def test_query_derived_blocks_dangerous_keywords(service):
    """Dangerous keywords should be blocked even inside SELECT."""
    with pytest.raises(ValueError, match="blocked keyword"):
        service.query_derived("SELECT * FROM commute_data; DROP TABLE commute_data")

    with pytest.raises(ValueError, match="blocked keyword"):
        service.query_derived("SELECT COPY 'data' TO '/tmp/evil.csv'")


def test_query_derived_allows_valid_select(db, derived_dir):
    """Valid SELECT queries should work."""
    _seed_commute(db)
    _rebuild_all(db, derived_dir)

    svc = CommuteService(db=db, derived_dir=derived_dir)
    result = svc.query_derived("SELECT count(*) as cnt FROM commute_data")
    assert len(result) == 1
    assert result[0]["cnt"] > 0


# ── Fix 6: Batch segments endpoint ──────────────────────────────────────────


def test_batch_segments_empty(client):
    """GET /segments should return empty list when no data."""
    resp = client.get("/api/v1/segments")
    assert resp.status_code == 200
    assert resp.json() == []


def test_batch_segments_returns_data(db, derived_dir):
    """GET /segments should return segments for all commutes in one call."""
    _seed_commute(db)
    _rebuild_all(db, derived_dir)

    svc = CommuteService(db=db, derived_dir=derived_dir)
    set_service(svc)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)

    resp = c.get("/api/v1/segments")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    # Each entry should have commute_id (batch endpoint includes it)
    assert "commute_id" in data[0]
    assert "segment_id" in data[0]
    assert "transport_mode" in data[0]


def test_batch_segments_direction_filter(db, derived_dir):
    """GET /segments?direction=morning should filter by direction."""
    _seed_commute(db)
    _rebuild_all(db, derived_dir)

    svc = CommuteService(db=db, derived_dir=derived_dir)
    set_service(svc)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)

    # Should return data or empty — but should not error
    resp = c.get("/api/v1/segments?direction=morning")
    assert resp.status_code == 200

    resp = c.get("/api/v1/segments?direction=nonexistent")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Fix 7: Bulk labels accepts both formats ──────────────────────────────────


def test_bulk_labels_bare_array(client):
    """POST /labels/bulk should accept a bare JSON array."""
    labels = [
        {
            "commute_id": "c1",
            "segment_id": 0,
            "original_mode": "driving",
            "corrected_mode": "train",
        },
    ]
    resp = client.post("/api/v1/labels/bulk", json=labels)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_bulk_labels_wrapped_format(client):
    """POST /labels/bulk should accept {"labels": [...]} format."""
    wrapped = {
        "labels": [
            {
                "commute_id": "c2",
                "segment_id": 0,
                "original_mode": "stationary",
                "corrected_mode": "waiting",
            },
            {
                "commute_id": "c2",
                "segment_id": 1,
                "original_mode": "driving",
                "corrected_mode": "train",
            },
        ]
    }
    resp = client.post("/api/v1/labels/bulk", json=wrapped)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_bulk_labels_both_formats_persist(client):
    """Both formats should persist labels identically."""
    # Bare array
    client.post(
        "/api/v1/labels/bulk",
        json=[
            {
                "commute_id": "c3",
                "segment_id": 0,
                "original_mode": "driving",
                "corrected_mode": "train",
            }
        ],
    )
    # Wrapped
    client.post(
        "/api/v1/labels/bulk",
        json={
            "labels": [
                {
                    "commute_id": "c3",
                    "segment_id": 1,
                    "original_mode": "stationary",
                    "corrected_mode": "waiting",
                }
            ]
        },
    )

    resp = client.get("/api/v1/labels?commute_id=c3")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Fix 8: Vectorized day-of-week replace ────────────────────────────────────


def test_polars_replace_day_names():
    """Vectorized replace should produce correct day names (replaces map_elements)."""
    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _DOW_MAP = {i: name for i, name in enumerate(DAY_NAMES)}

    df = pl.DataFrame({"day_of_week": [0, 1, 2, 3, 4, 5, 6]})
    result = df.with_columns(
        pl.col("day_of_week").replace_strict(_DOW_MAP, default="?").alias("day_name"),
    )

    assert result["day_name"].to_list() == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def test_polars_replace_unknown_day():
    """Unknown day-of-week values should get '?' default."""
    _DOW_MAP = {i: name for i, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}

    df = pl.DataFrame({"day_of_week": [0, 99]})
    result = df.with_columns(
        pl.col("day_of_week").replace_strict(_DOW_MAP, default="?").alias("day_name"),
    )

    assert result["day_name"].to_list() == ["Mon", "?"]


# ── Fix 10: .mcp.json transport type (#5) ────────────────────────────────────


def test_mcp_json_uses_http_type():
    """'.mcp.json must use "type": "http", not "type": "url".

    Claude Code only recognizes "http" for Streamable HTTP transport.
    "url" silently fails — the server doesn't register and no tools appear.
    See: https://github.com/jflammia/commuteTracker/issues/5
    """
    import json
    from pathlib import Path

    mcp_json_path = Path(__file__).parent.parent / ".mcp.json"
    assert mcp_json_path.exists(), ".mcp.json missing from repo root"

    config = json.loads(mcp_json_path.read_text())
    servers = config.get("mcpServers", {})
    assert len(servers) > 0, "No MCP servers configured"

    for name, server_config in servers.items():
        server_type = server_config.get("type", "")
        assert server_type == "http", (
            f"MCP server '{name}' uses type '{server_type}', must be 'http'"
        )


def test_mcp_docs_no_type_url():
    """Docs must not contain 'type': 'url' — only 'type': 'http' is valid.

    See: https://github.com/jflammia/commuteTracker/issues/5
    """
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    docs_to_check = [
        repo_root / "docs" / "mcp-integration.md",
        repo_root / "README.md",
    ]
    for doc_path in docs_to_check:
        if doc_path.exists():
            content = doc_path.read_text()
            assert '"type": "url"' not in content, (
                f'{doc_path.name} contains \'"type": "url"\' — must be \'"type": "http"\''
            )


# ── Fix 11: Container data paths (#6) ────────────────────────────────────────


def test_config_data_dirs_under_data_when_data_exists():
    """When /data exists (container), derived/raw dirs should be under /data.

    In container deployments, the only writable volume is /data. Defaulting
    DERIVED_DATA_DIR and RAW_DATA_DIR to PROJECT_ROOT/derived and
    PROJECT_ROOT/raw causes Permission denied errors.
    See: https://github.com/jflammia/commuteTracker/issues/6
    """
    import importlib
    from unittest.mock import patch as _patch

    # Simulate container environment: /data exists, no env vars set
    with (
        _patch("os.environ", {}),
        _patch("pathlib.Path.is_dir", return_value=True),
    ):
        # Reload config to pick up mocked environment
        import src.config

        importlib.reload(src.config)

        derived = str(src.config.DERIVED_DATA_DIR)
        raw = str(src.config.RAW_DATA_DIR)

    # Restore real config
    importlib.reload(src.config)

    assert "/data" in derived, f"DERIVED_DATA_DIR should be under /data, got: {derived}"
    assert "/data" in raw, f"RAW_DATA_DIR should be under /data, got: {raw}"


# ── Fix 12: Home geofence radius default (#7) ────────────────────────────────


def test_home_radius_default_is_50m():
    """HOME_RADIUS_M should default to 50m, not 150m.

    150m covers neighboring properties, causing false "at home" detection.
    50m tightly covers a single residential property.
    See: https://github.com/jflammia/commuteTracker/issues/7
    """
    import importlib
    from unittest.mock import patch as _patch

    with _patch.dict("os.environ", {}, clear=True):
        import src.config

        importlib.reload(src.config)
        home_radius = src.config.HOME_RADIUS_M
        work_radius = src.config.WORK_RADIUS_M

    importlib.reload(src.config)

    assert home_radius == 50.0, f"HOME_RADIUS_M default should be 50, got {home_radius}"
    assert work_radius == 150.0, f"WORK_RADIUS_M default should be 150, got {work_radius}"


# ── Fix 13: Timezone-aware timestamps (#11) ─────────────────────────────────


def test_enricher_timestamp_is_tz_aware_utc():
    """Enricher must produce timezone-aware UTC timestamps.

    Naive timestamps caused wrong date grouping and commute IDs when the
    server timezone differed from the user's timezone.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from src.processing.enricher import enrich

    df = pl.DataFrame(
        {
            "lat": [40.7128, 40.7130],
            "lon": [-74.0060, -74.0062],
            "tst": [1711440000, 1711440060],
        }
    )
    result = enrich(df)
    assert result["timestamp"].dtype == pl.Datetime("us", "UTC")


def test_enricher_adds_timezone_column():
    """Enricher must add a timezone column derived from GPS coordinates.

    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from src.processing.enricher import enrich

    df = pl.DataFrame(
        {
            "lat": [40.7128, 40.7130],
            "lon": [-74.0060, -74.0062],
            "tst": [1711440000, 1711440060],
        }
    )
    result = enrich(df)
    assert "timezone" in result.columns
    assert result["timezone"][0] == "America/New_York"


def test_enricher_adds_timestamp_local():
    """Enricher must add a timestamp_local column in the point's local timezone.

    timestamp_local is a naive datetime representing local time, used for
    date grouping and commute ID generation.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from datetime import datetime, timezone as dt_timezone
    from zoneinfo import ZoneInfo

    from src.processing.enricher import enrich

    tst = 1711440000  # 2024-03-26T08:00:00Z
    df = pl.DataFrame(
        {
            "lat": [40.7128],
            "lon": [-74.0060],
            "tst": [tst],
        }
    )
    result = enrich(df)
    assert "timestamp_local" in result.columns

    # Verify the local time matches manual conversion
    utc_dt = datetime.fromtimestamp(tst, tz=dt_timezone.utc)
    expected_local = utc_dt.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    actual_local = result["timestamp_local"][0]
    assert actual_local == expected_local


def test_enricher_late_night_utc_correct_local_date():
    """A point at 03:00 UTC should have a local date of the previous day in EDT.

    This is the core bug: 03:00 UTC = 23:00 EDT previous day, so the local
    date should be the day before the UTC date.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from datetime import date

    from src.processing.enricher import enrich

    # 2024-03-27 03:00:00 UTC = 2024-03-26 23:00:00 EDT
    tst = 1711508400
    df = pl.DataFrame(
        {
            "lat": [40.7128],
            "lon": [-74.0060],
            "tst": [tst],
        }
    )
    result = enrich(df)
    local_date = result["timestamp_local"].dt.date()[0]
    assert local_date == date(2024, 3, 26), f"Expected 2024-03-26, got {local_date}"


def test_commute_id_uses_local_date():
    """Commute IDs must use local date, not UTC date.

    A commute starting at 03:00 UTC in America/New_York (23:00 EDT previous
    day) should have the previous day's date in its commute ID.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from unittest.mock import patch as _patch

    # Base time: 2024-03-27 03:00 UTC = 2024-03-26 23:00 EDT
    base_tst = 1711508400

    # Build points: at home, transit, at work (all in same UTC "day" March 27)
    rows = []
    # At home (3 points)
    for i in range(3):
        rows.append({"_type": "location", "lat": HOME[0], "lon": HOME[1], "tst": base_tst + i * 10})
    # Transit (10 points)
    for i in range(1, 11):
        frac = i / 10
        lat = HOME[0] + (WORK[0] - HOME[0]) * frac
        lon = HOME[1] + (WORK[1] - HOME[1]) * frac
        rows.append({"_type": "location", "lat": lat, "lon": lon, "tst": base_tst + 30 + i * 10})
    # At work (3 points)
    for i in range(3):
        rows.append(
            {"_type": "location", "lat": WORK[0], "lon": WORK[1], "tst": base_tst + 140 + i * 10}
        )

    df = pl.DataFrame(rows)
    with _patch.multiple(
        "src.processing.pipeline",
        HOME_LAT=HOME[0],
        HOME_LON=HOME[1],
        HOME_RADIUS_M=200.0,
        WORK_LAT=WORK[0],
        WORK_LON=WORK[1],
        WORK_RADIUS_M=200.0,
    ):
        result = process_locations(df)

    commute_ids = result["commute_id"].drop_nulls().unique().to_list()
    assert len(commute_ids) == 1

    # Commute ID should use local date (2024-03-26), not UTC date (2024-03-27)
    cid = commute_ids[0]
    assert cid.startswith("2024-03-26"), f"Expected local date 2024-03-26, got commute ID: {cid}"


def test_parquet_file_named_by_local_date(db, derived_dir):
    """Parquet files must be named by local date, not UTC date.

    A point at 03:00 UTC in EDT (23:00 previous day) should land in the
    previous day's Parquet file.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    from pathlib import Path

    # 2024-03-27 03:30 UTC = 2024-03-26 23:30 EDT
    tst = 1711510200
    _insert_location(db, 40.7128, -74.0060, tst)

    with _pipeline_config():
        process_from_db(db, output_dir=derived_dir)

    # Should create a file for 2024-03-26 (local date), not 2024-03-27 (UTC date)
    files = list(Path(derived_dir).rglob("*.parquet"))
    assert len(files) == 1
    assert "2024-03-26" in files[0].name, f"Expected local date 2024-03-26, got {files[0].name}"


def test_pipeline_filter_interprets_dates_as_local_tz(db, derived_dir):
    """since/until filters must be interpreted as local timezone dates.

    When filtering for 2024-03-26 in America/New_York, the range should be
    2024-03-26 00:00 EDT (04:00 UTC) to 2024-03-26 23:59 EDT (03:59 UTC next day).
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    # Point at 2024-03-26 12:00 EDT = 2024-03-26 16:00 UTC (tst=1711468800)
    tst_in_range = 1711468800
    _insert_location(db, 40.7128, -74.0060, tst_in_range)

    # Point at 2024-03-26 03:00 UTC = 2024-03-25 23:00 EDT (should be EXCLUDED)
    tst_out_of_range = 1711422000
    _insert_location(db, 40.7128, -74.0060, tst_out_of_range)

    with _pipeline_config():
        results = process_from_db(
            db, output_dir=derived_dir, filters={"since": "2024-03-26", "until": "2024-03-26"}
        )
    assert results["total_records"] == 1


def test_daily_summary_uses_local_date(db, derived_dir):
    """get_daily_summary must query by local date, not UTC date.

    A point at 03:30 UTC in EDT (23:30 previous day) should be returned
    when querying the previous day's local date.
    See: https://github.com/jflammia/commuteTracker/issues/11
    """
    # 2024-03-27 03:30 UTC = 2024-03-26 23:30 EDT
    tst = 1711510200
    _insert_location(db, 40.7128, -74.0060, tst)
    _rebuild_all(db, derived_dir)

    store = DerivedStore(derived_dir)

    # Query by local date should find the point
    result = store.get_daily_summary("2024-03-26")
    assert not result.is_empty(), "Point at 23:30 EDT should be in 2024-03-26 local summary"

    # Query by UTC date should NOT find it (it's in a different local day)
    result_utc = store.get_daily_summary("2024-03-27")
    assert result_utc.is_empty(), "Point at 23:30 EDT should NOT be in 2024-03-27 local summary"
