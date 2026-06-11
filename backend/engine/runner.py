"""Owns the live engine + derived store. Startup performs a full rebuild so
live state continues seamlessly from history (an in-progress trip at the end
of the archive stays open in the live engine)."""

import logging

from backend.config import Settings
from backend.engine.machine import TripEngine
from backend.engine.rebuild import rebuild
from backend.engine.types import Point, TripClosed
from backend.optimizer.legs import decompose_trip
from backend.storage.derived import DerivedStore
from backend.transit.matcher import match_trip

log = logging.getLogger(__name__)


class EngineRunner:
    def __init__(self, engine: TripEngine, store: DerivedStore):
        self.engine = engine
        self.store = store

    @classmethod
    def start(cls, settings: Settings) -> "EngineRunner":
        engine, store, counts = rebuild(settings)
        log.info("engine runner started after rebuild: %s", counts)
        return cls(engine, store)

    def process_payload(self, payload: dict) -> None:
        point = Point.from_owntracks(payload)
        if point is None:
            return
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
            else:
                self.store.write_rejected(ev)

    def close(self) -> None:
        self.store.close()
