"""Truncate the derived store and replay the entire archive + raw tail
through a fresh engine. The returned engine carries live state (an
in-progress trip survives into live processing).

CLI: python -m backend.engine.rebuild
"""

import json
import logging
from collections import Counter
from datetime import UTC, datetime

from backend.config import Settings, load_settings
from backend.engine.checkpoint import RebuildCheckpoint
from backend.engine.geofence import geofences_from_settings
from backend.engine.machine import TripEngine
from backend.engine.params import EngineParams
from backend.engine.types import Point, TripClosed
from backend.optimizer.legs import decompose_trip
from backend.storage.derived import DerivedStore
from backend.storage.query import EventQuery
from backend.transit.gtfs import latest_snapshot, parse_gtfs
from backend.transit.matcher import match_trip

log = logging.getLogger(__name__)


def rebuild(
    settings: Settings, params: EngineParams | None = None, *, incremental: bool = False
) -> tuple[TripEngine, DerivedStore, dict]:
    store = DerivedStore(settings)
    engine = TripEngine(params or EngineParams(), geofences_from_settings(settings))

    # Decide full vs incremental. A checkpoint is honored only when the caller
    # asked for an incremental rebuild AND a valid one exists; otherwise we
    # truncate and replay everything from scratch (the original behavior). The
    # checkpoint's hwm is a DuckDB `CAST(received_at AS VARCHAR)` string written
    # by a prior run — it never originates from external input.
    cp = RebuildCheckpoint(settings.data_dir).load() if incremental else None
    if cp is None:
        store.truncate()
        hwm = None
    else:
        engine.state = cp.engine_state
        hwm = cp.hwm

    # GTFS is always re-parsed from the latest snapshots (parse_gtfs is
    # DELETE-by-source then INSERT, so re-parsing on a non-truncated store is
    # idempotent and keeps the schedule current).
    for source in ("gtfs_path", "gtfs_njt"):
        snapshot = latest_snapshot(settings, source)
        if snapshot is not None:
            parse_gtfs(store.con, source, snapshot, fetched_at=datetime.now(UTC).isoformat())

    q = EventQuery(settings)
    rel = q.events("owntracks")
    where = ""
    if hwm is not None:
        # Embed a validated timestamp literal: EventQuery.sql binds only
        # relations, not parameters. hwm is our own checkpoint string (safe).
        where = f"WHERE received_at > CAST('{hwm}' AS TIMESTAMPTZ)"
    rows = q.sql(
        f"SELECT CAST(received_at AS VARCHAR), CAST(payload AS VARCHAR) FROM rel {where} "
        f"ORDER BY received_at",
        rel=rel,
    ).fetchall()
    counts: Counter = Counter()
    last_received_at = None
    for received_at_str, payload_text in rows:
        last_received_at = received_at_str  # rows are ASC; final value is the max
        point = Point.from_owntracks(json.loads(payload_text))
        if point is None:
            counts["skipped"] += 1
            continue
        counts["points"] += 1
        for ev in engine.process(point):
            if isinstance(ev, TripClosed):
                store.write_trip_closed(ev)
                counts["trips"] += 1
                # Unlike the live path, matcher errors here propagate: rebuild inputs
                # are fully controlled derived data, so a crash means a real bug.
                train_matches = match_trip(store.con, ev)
                store.write_train_matches(train_matches)
                counts["train_matches"] += len(train_matches)
                legs = decompose_trip(store.get_trip(ev.trip.trip_id))
                store.write_leg_observations(ev.trip.trip_id, legs)
                counts["leg_observations"] += len(legs)
            else:
                store.write_rejected(ev)
                counts["rejected"] += 1
    # Replay label events last — label supremacy over heuristics and matches.
    rel = q.events("labels")
    label_rows = q.sql(
        "SELECT CAST(payload AS VARCHAR) FROM rel ORDER BY received_at", rel=rel
    ).fetchall()
    for (payload_text,) in label_rows:
        if store.apply_label(json.loads(payload_text)):
            counts["labels_applied"] += 1
        else:
            counts["labels_skipped"] += 1

    # Persist a checkpoint so a later incremental rebuild can resume past it.
    # The hwm advances to the last processed owntracks event; if this run saw
    # no new events it retains the prior hwm (no checkpoint when neither exists).
    new_hwm = last_received_at if last_received_at is not None else hwm
    if new_hwm is not None:
        RebuildCheckpoint(settings.data_dir).save(hwm=new_hwm, engine_state=engine.state)

    log.info("rebuild complete: %s", dict(counts))
    return engine, store, dict(counts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _, _, report = rebuild(load_settings())
    print(json.dumps(report, indent=2))
