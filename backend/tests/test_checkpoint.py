import json

from backend.engine.checkpoint import RebuildCheckpoint
from backend.engine.machine import EngineState


def test_save_and_load_roundtrip(tmp_path):
    cp = RebuildCheckpoint(tmp_path)
    state = EngineState(status="moving", geofence="home")
    cp.save(hwm="2026-06-11T12:00:00+00:00", engine_state=state)
    loaded = cp.load()
    assert loaded is not None
    assert loaded.hwm == "2026-06-11T12:00:00+00:00"
    assert loaded.engine_state.status == "moving"
    assert loaded.engine_state.geofence == "home"


def test_load_returns_none_when_absent(tmp_path):
    assert RebuildCheckpoint(tmp_path).load() is None


def test_load_returns_none_on_corruption(tmp_path):
    cp = RebuildCheckpoint(tmp_path)
    cp.path.parent.mkdir(parents=True, exist_ok=True)
    cp.path.write_text("{not json")
    assert cp.load() is None


def test_load_returns_none_on_version_mismatch(tmp_path):
    cp = RebuildCheckpoint(tmp_path)
    cp.save(hwm="x", engine_state=EngineState())
    d = json.loads(cp.path.read_text())
    d["version"] = 999
    cp.path.write_text(json.dumps(d))
    assert cp.load() is None


def test_save_is_atomic_leaves_no_tmp(tmp_path):
    cp = RebuildCheckpoint(tmp_path)
    cp.save(hwm="x", engine_state=EngineState())
    assert cp.path.exists()
    assert not cp.path.with_suffix(".json.tmp").exists()
