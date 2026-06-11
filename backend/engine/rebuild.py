"""Truncate the derived store and replay the entire archive + raw tail
through a fresh engine. The returned engine carries live state (an
in-progress trip survives into live processing).

CLI: python -m backend.engine.rebuild
"""

import json
import logging
from collections import Counter

from backend.config import Settings, load_settings
from backend.engine.geofence import geofences_from_settings
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import Point, TripClosed
from backend.storage.derived import DerivedStore
from backend.storage.query import EventQuery

log = logging.getLogger(__name__)


def rebuild(
    settings: Settings, params: EngineParams | None = None
) -> tuple[TripEngine, DerivedStore, dict]:
    store = DerivedStore(settings)
    store.truncate()
    engine = TripEngine(params or EngineParams(), geofences_from_settings(settings))
    q = EventQuery(settings)
    rel = q.events("owntracks")
    rows = q.sql(
        "SELECT CAST(payload AS VARCHAR) FROM rel ORDER BY received_at", rel=rel
    ).fetchall()
    counts: Counter = Counter()
    for (payload_text,) in rows:
        point = Point.from_owntracks(json.loads(payload_text))
        if point is None:
            counts["skipped"] += 1
            continue
        counts["points"] += 1
        for ev in engine.process(point):
            if isinstance(ev, TripClosed):
                store.write_trip_closed(ev)
                counts["trips"] += 1
            else:
                store.write_rejected(ev)
                counts["rejected"] += 1
    log.info("rebuild complete: %s", dict(counts))
    return engine, store, dict(counts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _, _, report = rebuild(load_settings())
    print(json.dumps(report, indent=2))
