"""Owns the live engine + derived store. Startup performs a full rebuild so
live state continues seamlessly from history (an in-progress trip at the end
of the archive stays open in the live engine)."""

import logging

from backend.config import Settings
from backend.engine.checkpoint import RebuildCheckpoint
from backend.engine.machine import TripEngine
from backend.engine.rebuild import rebuild
from backend.engine.types import Point, TripClosed
from backend.optimizer.legs import decompose_trip
from backend.storage.derived import DerivedStore
from backend.transit.matcher import match_trip

log = logging.getLogger(__name__)


class EngineRunner:
    def __init__(
        self,
        engine: TripEngine,
        store: DerivedStore,
        checkpoint: RebuildCheckpoint | None = None,
    ):
        self.engine = engine
        self.store = store
        self._checkpoint = checkpoint
        self._last_received_at: str | None = None

    @classmethod
    def start(cls, settings: Settings) -> "EngineRunner":
        engine, store, counts = rebuild(settings, incremental=True)
        log.info("engine runner started after rebuild: %s", counts)
        return cls(engine, store, RebuildCheckpoint(settings.data_dir))

    def process_payload(self, payload: dict, received_at: str | None = None) -> None:
        point = Point.from_owntracks(payload)
        if point is None:
            return
        if received_at is not None:
            self._last_received_at = received_at
        for ev in self.engine.process(point):
            if isinstance(ev, TripClosed):
                self.store.write_trip_closed(ev)
                try:
                    self.store.write_train_matches(match_trip(self.store.con, ev))
                except Exception:
                    log.exception("train matching failed — trip stored unmatched")
                try:
                    self.store.write_leg_observations(
                        ev.trip.trip_id, decompose_trip(self.store.get_trip(ev.trip.trip_id))
                    )
                except Exception:
                    log.exception("leg decomposition failed — trip stored without legs")
                # A closed trip is a clean idle boundary: the engine is back to
                # idle with `prev` = the just-processed point, so received_at is a
                # consistent hwm for the next incremental boot.
                if self._checkpoint is not None and received_at is not None:
                    self._checkpoint.save(hwm=received_at, engine_state=self.engine.state)
            else:
                self.store.write_rejected(ev)

    def close(self) -> None:
        # Persist a final checkpoint capturing any in-progress engine state so the
        # next boot replays only events past the last point we processed.
        if self._checkpoint is not None and self._last_received_at is not None:
            self._checkpoint.save(hwm=self._last_received_at, engine_state=self.engine.state)
        self.store.close()
