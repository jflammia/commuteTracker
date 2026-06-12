"""Persisted boot-rebuild checkpoint: the last-processed event timestamp plus
the serialized engine state, so a restart replays only newer events."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from backend.engine.machine import EngineState

_VERSION = 1


@dataclass
class Checkpoint:
    hwm: str  # received_at (ISO-8601) of the last event reflected in engine_state + store
    engine_state: EngineState


class RebuildCheckpoint:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "derived" / "rebuild_checkpoint.json"

    def load(self) -> Checkpoint | None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("version") != _VERSION:
                return None
            return Checkpoint(hwm=d["hwm"], engine_state=EngineState.from_dict(d["engine_state"]))
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def save(self, *, hwm: str, engine_state: EngineState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _VERSION, "hwm": hwm, "engine_state": engine_state.to_dict()}
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)  # atomic
