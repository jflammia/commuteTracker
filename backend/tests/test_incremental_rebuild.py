"""End-to-end correctness gate for incremental rebuild.

The incremental path must produce a derived store byte-for-byte identical to a
full rebuild over the same events, at every split point (including mid-trip).
This proves the engine's replay==live property survives EngineState
serialization AND the idempotent derived writes, across the checkpoint boundary.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.config import Settings
from backend.engine.checkpoint import RebuildCheckpoint
from backend.engine.rebuild import rebuild
from backend.tests.synth import commute


def _settings(data_dir: Path) -> Settings:
    """Mirror the conftest `settings` fixture but for an arbitrary data_dir,
    geofences unset so geofences_from_settings returns []."""
    return Settings(
        data_dir=data_dir,
        s3_bucket=None,
        s3_prefix="commute-tracker",
        s3_region=None,
        passthrough_url=None,
        archive_hour_utc=6,
    )


def _write_raw(data_dir, points):
    """Write Points as owntracks raw JSONL day-files (received_at from ts)."""
    by_day = {}
    for p in points:
        iso = datetime.fromtimestamp(p.ts, tz=UTC).isoformat()
        rec = {
            "received_at": iso,
            "user": "justin",
            "device": "iphone",
            "payload": {
                "_type": "location",
                "tst": int(p.ts),
                "lat": p.lat,
                "lon": p.lon,
                "acc": p.accuracy_m,
            },
        }
        by_day.setdefault(iso[:10], []).append(json.dumps(rec))
    d = data_dir / "raw" / "owntracks"
    d.mkdir(parents=True, exist_ok=True)
    for day, lines in by_day.items():
        (d / f"{day}.jsonl").write_text("\n".join(lines) + "\n")


def _snapshot(con):
    tables = ("trips", "segments", "trip_points", "train_matches", "leg_observations")
    return {t: con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in tables}


def _stream():
    """Two chained commutes so a trip can span a split point."""
    pts1, _, lat_end = commute(t0=1_781_100_000.0)
    pts2, _, _ = commute(t0=1_781_130_000.0, lat0=lat_end)
    return pts1 + pts2


def _diff(snap_a, snap_b):
    """Human-readable per-table difference for failure messages."""
    out = []
    for t in snap_a:
        if snap_a[t] != snap_b[t]:
            out.append(f"  table {t}: a={len(snap_a[t])} rows b={len(snap_b[t])} rows")
            for ra, rb in zip(snap_a[t], snap_b[t]):
                if ra != rb:
                    out.append(f"    a: {ra}\n    b: {rb}")
                    break
            if len(snap_a[t]) != len(snap_b[t]):
                out.append("    (row count differs)")
    return "\n".join(out) or "  (no row-level diff found)"


STREAM = _stream()


@pytest.mark.parametrize("split", [len(STREAM) // 4, len(STREAM) // 2, len(STREAM) - 5])
def test_incremental_equals_full_rebuild(tmp_path, split):
    # --- Full rebuild over the whole stream ---
    full_dir = tmp_path / "full"
    _write_raw(full_dir, STREAM)
    _, s_full, _ = rebuild(_settings(full_dir))
    snap_full = _snapshot(s_full.con)

    # --- Incremental: first run over the prefix (no checkpoint → full prefix) ---
    inc_dir = tmp_path / "inc"
    _write_raw(inc_dir, STREAM[:split])
    rebuild(_settings(inc_dir), incremental=True)  # writes a checkpoint

    # --- Incremental: second run with the full stream present (tail only) ---
    _write_raw(inc_dir, STREAM)  # now all points present
    _, s_inc, _ = rebuild(_settings(inc_dir), incremental=True)
    snap_inc = _snapshot(s_inc.con)

    for table in snap_full:
        assert snap_inc[table] == snap_full[table], (
            f"split={split} table={table} diverged:\n{_diff(snap_full, snap_inc)}"
        )


def test_incremental_without_checkpoint_falls_back_to_full(tmp_path):
    full_dir = tmp_path / "full"
    _write_raw(full_dir, STREAM)
    _, s_full, _ = rebuild(_settings(full_dir))
    snap_full = _snapshot(s_full.con)

    inc_dir = tmp_path / "inc"
    _write_raw(inc_dir, STREAM)
    # incremental=True but no checkpoint exists → must behave like full rebuild
    _, s_inc, _ = rebuild(_settings(inc_dir), incremental=True)
    snap_inc = _snapshot(s_inc.con)

    for table in snap_full:
        assert snap_inc[table] == snap_full[table], _diff(snap_full, snap_inc)


def test_incremental_with_corrupt_checkpoint_falls_back(tmp_path):
    full_dir = tmp_path / "full"
    _write_raw(full_dir, STREAM)
    _, s_full, _ = rebuild(_settings(full_dir))
    snap_full = _snapshot(s_full.con)

    inc_dir = tmp_path / "inc"
    _write_raw(inc_dir, STREAM)
    cp_path = inc_dir / "derived" / "rebuild_checkpoint.json"
    cp_path.parent.mkdir(parents=True, exist_ok=True)
    cp_path.write_text("{ this is not valid json")

    _, s_inc, _ = rebuild(_settings(inc_dir), incremental=True)
    snap_inc = _snapshot(s_inc.con)

    for table in snap_full:
        assert snap_inc[table] == snap_full[table], _diff(snap_full, snap_inc)


def test_crash_reprocessing_is_idempotent(tmp_path):
    """A crash between a derived write and the checkpoint update leaves the hwm
    behind reality. On the next run we reprocess the overlap; idempotent writes
    must absorb it so the store still equals a full rebuild."""
    full_dir = tmp_path / "full"
    _write_raw(full_dir, STREAM)
    _, s_full, _ = rebuild(_settings(full_dir))
    snap_full = _snapshot(s_full.con)

    inc_dir = tmp_path / "inc"
    # First run over a prefix to establish a real engine state + checkpoint.
    split = len(STREAM) // 2
    _write_raw(inc_dir, STREAM[:split])
    rebuild(_settings(inc_dir), incremental=True)

    # Second run over the full stream (normal incremental tail).
    _write_raw(inc_dir, STREAM)
    rebuild(_settings(inc_dir), incremental=True)

    # Simulate a crash: rewind the saved hwm backward to an earlier event so the
    # next run reprocesses an overlap. The engine_state stays as last saved.
    cp_path = inc_dir / "derived" / "rebuild_checkpoint.json"
    cp = json.loads(cp_path.read_text())
    earlier = datetime.fromtimestamp(STREAM[split].ts - 1, tz=UTC).isoformat()
    cp["hwm"] = earlier
    cp_path.write_text(json.dumps(cp))

    _, s_inc, _ = rebuild(_settings(inc_dir), incremental=True)
    snap_inc = _snapshot(s_inc.con)

    for table in snap_full:
        assert snap_inc[table] == snap_full[table], _diff(snap_full, snap_inc)


def test_empty_archive_incremental_is_safe(tmp_path):
    inc_dir = tmp_path / "inc"
    # No raw files written at all.
    engine, store, counts = rebuild(_settings(inc_dir), incremental=True)
    snap = _snapshot(store.con)
    for table, rows in snap.items():
        assert rows == [], f"{table} should be empty, got {rows}"
    # No checkpoint should be written when there are no events.
    assert RebuildCheckpoint(inc_dir).load() is None
