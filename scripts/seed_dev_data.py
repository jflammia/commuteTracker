#!/usr/bin/env python3
"""Seed a local dev environment with realistic synthetic commute data.

Generates multi-modal NJ-to-Manhattan commute GPS data, inserts it into the
database, runs the processing pipeline, and adds sample label corrections.

Usage:
    python scripts/seed_dev_data.py              # 20 weekdays of data
    python scripts/seed_dev_data.py --days 5     # Quick 1-week seed
    python scripts/seed_dev_data.py --days 60    # Larger dataset
    python scripts/seed_dev_data.py --clean      # Wipe existing data first
    python scripts/seed_dev_data.py --force      # Skip confirmation prompts
"""

import argparse
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Geofence coordinates ────────────────────────────────────────────────────

HOME_LAT, HOME_LON = 40.8127, -74.2090
WORK_LAT, WORK_LON = 40.7536, -73.9832
GEOFENCE_RADIUS_M = 200

# ── Route waypoints ─────────────────────────────────────────────────────────
# Morning: drive -> walk -> wait -> train -> walk
# Evening: walk -> wait -> train -> walk -> drive

# Drive from home to train station parking area
STATION_PARKING_LAT, STATION_PARKING_LON = 40.7678, -73.9903

# Walk from parking to platform
PLATFORM_LAT, PLATFORM_LON = 40.7668, -73.9898

# Train arrives at Penn Station
PENN_STATION_LAT, PENN_STATION_LON = 40.7506, -73.9935

# Walk from Penn Station to office (Bryant Park area)
# WORK_LAT, WORK_LON defined above

# ── Speed profiles (km/h) ───────────────────────────────────────────────────

SPEED_PROFILES = {
    "driving": (25.0, 50.0),
    "walking": (4.0, 6.0),
    "waiting": (0.0, 0.5),
    "train": (50.0, 90.0),
}

# ── Point generation ────────────────────────────────────────────────────────

GPS_JITTER = 0.00005  # ~5 meters of random noise
POINT_INTERVAL_S = 10  # seconds between GPS points


def _jitter() -> float:
    return random.uniform(-GPS_JITTER, GPS_JITTER)


def _interpolate_points(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    start_tst: int,
    duration_s: int,
) -> list[dict]:
    """Generate GPS points along a straight line between two coordinates."""
    n_points = max(2, duration_s // POINT_INTERVAL_S)
    points = []

    for i in range(n_points):
        frac = i / (n_points - 1) if n_points > 1 else 0.0
        lat = start_lat + (end_lat - start_lat) * frac + _jitter()
        lon = start_lon + (end_lon - start_lon) * frac + _jitter()
        tst = start_tst + int(duration_s * (i / (n_points - 1))) if n_points > 1 else start_tst

        # Calculate approximate velocity from speed profile
        dist_m = _haversine_m(start_lat, start_lon, end_lat, end_lon)
        avg_speed_ms = dist_m / max(duration_s, 1)

        points.append({
            "_type": "location",
            "tid": "ph",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "alt": random.randint(10, 25),
            "acc": random.randint(5, 15),
            "vel": max(0, int(avg_speed_ms + random.uniform(-1, 1))),
            "tst": tst,
            "batt": random.randint(75, 95),
            "conn": "w",
        })

    return points


def _stationary_points(
    lat: float,
    lon: float,
    start_tst: int,
    duration_s: int,
) -> list[dict]:
    """Generate near-stationary GPS points at a single location."""
    n_points = max(2, duration_s // POINT_INTERVAL_S)
    points = []

    for i in range(n_points):
        tst = start_tst + int(duration_s * (i / (n_points - 1))) if n_points > 1 else start_tst
        points.append({
            "_type": "location",
            "tid": "ph",
            "lat": round(lat + _jitter(), 6),
            "lon": round(lon + _jitter(), 6),
            "alt": random.randint(10, 25),
            "acc": random.randint(5, 15),
            "vel": 0,
            "tst": tst,
            "batt": random.randint(75, 95),
            "conn": "w",
        })

    return points


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def _vary(base_seconds: int, pct: float = 0.10) -> int:
    """Add random variance to a duration."""
    delta = int(base_seconds * pct)
    return base_seconds + random.randint(-delta, delta)


# ── Commute generation ──────────────────────────────────────────────────────

# Leg durations in seconds (base values)
MORNING_LEGS = {
    "drive_to_station": 20 * 60,   # 20 min drive
    "walk_to_platform": 3 * 60,    # 3 min walk from parking
    "wait_on_platform": 2 * 60,    # 2 min wait
    "train_to_penn": 20 * 60,      # 20 min train ride
    "walk_to_office": 7 * 60,      # 7 min walk
}

EVENING_LEGS = {
    "walk_to_penn": 7 * 60,
    "wait_on_platform": 2 * 60,
    "train_to_station": 20 * 60,
    "walk_to_parking": 3 * 60,
    "drive_home": 20 * 60,
}


def generate_morning_commute(base_departure: datetime) -> list[dict]:
    """Generate GPS points for a morning commute: drive, walk, wait, train, walk."""
    points = []
    tst = int(base_departure.timestamp())

    # Start with a few points at home (inside geofence)
    points.extend(_stationary_points(HOME_LAT, HOME_LON, tst, 30))
    tst += 30

    # Leg 1: Drive home -> train station parking
    dur = _vary(MORNING_LEGS["drive_to_station"])
    points.extend(_interpolate_points(
        HOME_LAT, HOME_LON,
        STATION_PARKING_LAT, STATION_PARKING_LON,
        tst, dur,
    ))
    tst += dur

    # Leg 2: Walk parking -> platform
    dur = _vary(MORNING_LEGS["walk_to_platform"])
    points.extend(_interpolate_points(
        STATION_PARKING_LAT, STATION_PARKING_LON,
        PLATFORM_LAT, PLATFORM_LON,
        tst, dur,
    ))
    tst += dur

    # Leg 3: Wait on platform
    dur = _vary(MORNING_LEGS["wait_on_platform"], pct=0.30)
    points.extend(_stationary_points(PLATFORM_LAT, PLATFORM_LON, tst, dur))
    tst += dur

    # Leg 4: Train platform -> Penn Station
    dur = _vary(MORNING_LEGS["train_to_penn"])
    points.extend(_interpolate_points(
        PLATFORM_LAT, PLATFORM_LON,
        PENN_STATION_LAT, PENN_STATION_LON,
        tst, dur,
    ))
    tst += dur

    # Leg 5: Walk Penn Station -> office
    dur = _vary(MORNING_LEGS["walk_to_office"])
    points.extend(_interpolate_points(
        PENN_STATION_LAT, PENN_STATION_LON,
        WORK_LAT, WORK_LON,
        tst, dur,
    ))
    tst += dur

    # End with a few points at work (inside geofence)
    points.extend(_stationary_points(WORK_LAT, WORK_LON, tst, 30))

    return points


def generate_evening_commute(base_departure: datetime) -> list[dict]:
    """Generate GPS points for an evening commute: walk, wait, train, walk, drive."""
    points = []
    tst = int(base_departure.timestamp())

    # Start with a few points at work (inside geofence)
    points.extend(_stationary_points(WORK_LAT, WORK_LON, tst, 30))
    tst += 30

    # Leg 1: Walk office -> Penn Station
    dur = _vary(EVENING_LEGS["walk_to_penn"])
    points.extend(_interpolate_points(
        WORK_LAT, WORK_LON,
        PENN_STATION_LAT, PENN_STATION_LON,
        tst, dur,
    ))
    tst += dur

    # Leg 2: Wait on platform
    dur = _vary(EVENING_LEGS["wait_on_platform"], pct=0.30)
    points.extend(_stationary_points(PENN_STATION_LAT, PENN_STATION_LON, tst, dur))
    tst += dur

    # Leg 3: Train Penn Station -> suburban station
    dur = _vary(EVENING_LEGS["train_to_station"])
    points.extend(_interpolate_points(
        PENN_STATION_LAT, PENN_STATION_LON,
        PLATFORM_LAT, PLATFORM_LON,
        tst, dur,
    ))
    tst += dur

    # Leg 4: Walk platform -> parking
    dur = _vary(EVENING_LEGS["walk_to_parking"])
    points.extend(_interpolate_points(
        PLATFORM_LAT, PLATFORM_LON,
        STATION_PARKING_LAT, STATION_PARKING_LON,
        tst, dur,
    ))
    tst += dur

    # Leg 5: Drive station -> home
    dur = _vary(EVENING_LEGS["drive_home"])
    points.extend(_interpolate_points(
        STATION_PARKING_LAT, STATION_PARKING_LON,
        HOME_LAT, HOME_LON,
        tst, dur,
    ))
    tst += dur

    # End with a few points at home (inside geofence)
    points.extend(_stationary_points(HOME_LAT, HOME_LON, tst, 30))

    return points


def generate_all_commutes(num_days: int, start_date: datetime | None = None) -> list[dict]:
    """Generate morning + evening commutes for N weekdays."""
    all_points = []

    if start_date is None:
        # Start num_days weekdays ago from today
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        # Walk backward to find enough weekdays
        d = today
        weekdays_found = 0
        while weekdays_found < num_days:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                weekdays_found += 1
        start_date = d

    current = start_date
    days_generated = 0

    while days_generated < num_days:
        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        # Morning: depart ~7:30 AM ET (+/- 15 min)
        morning_offset = random.randint(-15, 15)
        morning_departure = current.replace(
            hour=7, minute=30, second=0, microsecond=0
        ) + timedelta(minutes=morning_offset)
        all_points.extend(generate_morning_commute(morning_departure))

        # Evening: depart ~5:30 PM ET (+/- 15 min)
        evening_offset = random.randint(-15, 15)
        evening_departure = current.replace(
            hour=17, minute=30, second=0, microsecond=0
        ) + timedelta(minutes=evening_offset)
        all_points.extend(generate_evening_commute(evening_departure))

        days_generated += 1
        current += timedelta(days=1)

    return all_points


# ── Label corrections ───────────────────────────────────────────────────────

def insert_label_corrections(db, commute_ids: list[str]) -> int:
    """Insert realistic label corrections for a subset of commutes."""
    from src.storage.label_store import LabelStore

    label_store = LabelStore(db)
    corrections = []

    # Pick commutes from the first few days for corrections
    target_ids = commute_ids[:min(8, len(commute_ids))]

    correction_templates = [
        # (segment_id, original_mode, corrected_mode, notes)
        (0, "stationary", "waiting", "Was waiting on platform, not just stationary"),
        (2, "stationary", "waiting", "Platform wait before train"),
        (1, "driving", "train", "Speed overlap — this was actually the train"),
        (3, "driving", "walking", "Slow movement near station was walking, not driving"),
        (0, "stationary", "waiting", "Waiting at Penn Station platform"),
        (2, "driving", "train", "Misclassified — train segment at lower speed"),
        (1, "walking", "waiting", "Standing still at crosswalk, not walking"),
        (3, "stationary", "waiting", "Waiting for the train"),
    ]

    for i, cid in enumerate(target_ids):
        if i >= len(correction_templates):
            break
        seg_id, orig, corrected, notes = correction_templates[i]
        try:
            label_store.add_label(
                commute_id=cid,
                segment_id=seg_id,
                original_mode=orig,
                corrected_mode=corrected,
                notes=notes,
            )
            corrections.append((cid, seg_id, f"{orig} -> {corrected}"))
        except Exception as e:
            print(f"  Warning: could not add label for {cid} seg {seg_id}: {e}")

    return len(corrections)


# ── Database operations ─────────────────────────────────────────────────────

def clean_database(db) -> int:
    """Delete all records from location_records and segment_labels tables."""
    from src.storage.database import LocationRecord, SegmentLabelRecord

    total = 0
    with db.session() as session:
        total += session.query(LocationRecord).delete()
        total += session.query(SegmentLabelRecord).delete()
        session.commit()
    return total


def clean_derived(derived_dir: Path) -> int:
    """Delete all Parquet files in the derived directory."""
    removed = 0
    if derived_dir.exists():
        for pq in derived_dir.rglob("*.parquet"):
            pq.unlink()
            removed += 1
    return removed


def insert_records(db, points: list[dict]) -> int:
    """Bulk-insert GPS points into the database."""
    count = 0
    for p in points:
        payload = dict(p)
        payload["received_at"] = datetime.fromtimestamp(p["tst"], tz=timezone.utc).isoformat()
        payload["_receiver_user"] = "seeduser"
        payload["_receiver_device"] = "seedphone"
        db.insert_record(payload, user="seeduser", device="seedphone")
        count += 1
    return count


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed a dev environment with synthetic commute data"
    )
    parser.add_argument(
        "--days", type=int, default=20,
        help="Number of weekdays to generate (default: 20)",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Wipe existing DB records and derived Parquet files before seeding",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompts",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducible data (default: 42)",
    )
    args = parser.parse_args()

    random.seed(args.seed)

    # Set geofence env vars BEFORE importing src modules (config reads at import time)
    import os
    os.environ["HOME_LAT"] = str(HOME_LAT)
    os.environ["HOME_LON"] = str(HOME_LON)
    os.environ["HOME_RADIUS_M"] = str(GEOFENCE_RADIUS_M)
    os.environ["WORK_LAT"] = str(WORK_LAT)
    os.environ["WORK_LON"] = str(WORK_LON)
    os.environ["WORK_RADIUS_M"] = str(GEOFENCE_RADIUS_M)

    from src.config import DATABASE_URL, DERIVED_DATA_DIR
    from src.storage.database import Database

    # Ensure parent directory exists for SQLite (normally done by FastAPI lifespan)
    if DATABASE_URL.startswith("sqlite"):
        db_path = DATABASE_URL.replace("sqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    db = Database(DATABASE_URL)
    db.create_tables()
    derived_dir = Path(DERIVED_DATA_DIR)

    existing = db.count_records()

    # Handle existing data
    if args.clean:
        if not args.force:
            resp = input(f"This will delete {existing} existing records and all Parquet files. Continue? [y/N] ")
            if resp.lower() != "y":
                print("Aborted.")
                sys.exit(0)
        deleted_records = clean_database(db)
        deleted_parquet = clean_derived(derived_dir)
        print(f"Cleaned: {deleted_records} DB records, {deleted_parquet} Parquet files")
    elif existing > 0 and not args.force:
        resp = input(
            f"Database already has {existing} records. "
            f"Add seed data on top? (Use --clean to wipe first) [y/N] "
        )
        if resp.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    # Generate GPS data
    print(f"Generating {args.days} weekdays of commute data...")
    points = generate_all_commutes(args.days)
    print(f"  Generated {len(points)} GPS points")

    # Insert into database
    print("Inserting into database...")
    inserted = insert_records(db, points)
    print(f"  Inserted {inserted} records")

    # Run processing pipeline
    print("Running processing pipeline...")
    from src.processing.pipeline import process_from_db

    results = process_from_db(db, output_dir=derived_dir)
    print(f"  Processed {results['total_records']} records")
    print(f"  Found {results['commutes_found']} commutes")
    print(f"  Wrote {len(results['files_written'])} Parquet files")

    # Insert label corrections
    print("Adding label corrections...")
    # Find commute IDs from the derived data
    from src.storage.derived_store import DerivedStore
    store = DerivedStore(derived_dir)
    commutes_df = store.get_commutes()
    if not commutes_df.is_empty():
        commute_ids = commutes_df["commute_id"].to_list()
        n_labels = insert_label_corrections(db, commute_ids)
        print(f"  Added {n_labels} label corrections")
    else:
        print("  No commutes found — skipping label corrections")
        commute_ids = []

    # Summary
    print("\n" + "=" * 60)
    print("Seed complete!")
    print(f"  Records:  {inserted}")
    print(f"  Commutes: {results['commutes_found']}")
    print(f"  Parquet:  {len(results['files_written'])} files")
    print(f"  Labels:   {n_labels if not commutes_df.is_empty() else 0}")
    print()
    print("Add these to your .env for commute detection:")
    print()
    print(f"  HOME_LAT={HOME_LAT}")
    print(f"  HOME_LON={HOME_LON}")
    print(f"  HOME_RADIUS_M={GEOFENCE_RADIUS_M}")
    print(f"  WORK_LAT={WORK_LAT}")
    print(f"  WORK_LON={WORK_LON}")
    print(f"  WORK_RADIUS_M={GEOFENCE_RADIUS_M}")
    print()
    print("Start the receiver and dashboard:")
    print("  uvicorn src.receiver.app:app --host 0.0.0.0 --port 8080")
    print("  streamlit run src/dashboard/app.py")


if __name__ == "__main__":
    main()
