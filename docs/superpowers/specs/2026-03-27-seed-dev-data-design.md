# Seed Dev Data Script — Design Spec

## Goal

Create `scripts/seed_dev_data.py` that populates a local dev environment with realistic synthetic commute data, enabling developers to exercise all features (dashboard, API, MCP, labeling, ML training) without needing a real OwnTracks device.

## Route

NJ suburbs to Midtown Manhattan, multi-modal commute:

**Morning (home-to-work):**
1. **Drive** from home (Montclair, NJ: 40.8127, -74.2090) to train station area (~40.7678, -73.9903)
2. **Walk** from parking to platform
3. **Wait** on platform (~1-3 min stationary)
4. **Train** to Penn Station (40.7506, -73.9935)
5. **Walk** from Penn Station to office (Bryant Park area: 40.7536, -73.9832)

**Evening (work-to-home):** reverse order — walk, wait, train, walk, drive.

### Geofences

| Zone | Lat | Lon | Radius |
|------|-----|-----|--------|
| Home | 40.8127 | -74.2090 | 200m |
| Work | 40.7536 | -73.9832 | 200m |

## Data Generation

### GPS Points

Each leg generates OwnTracks-format JSON payloads at ~10-second intervals:

```json
{
  "_type": "location",
  "tid": "ph",
  "lat": 40.8127,
  "lon": -74.2090,
  "alt": 15,
  "acc": <5-15 random>,
  "vel": <speed-appropriate>,
  "tst": <unix timestamp>,
  "batt": <80-95 random>,
  "conn": "w"
}
```

Coordinates are linearly interpolated along each leg with small random jitter (~0.00005 degrees, ~5m) for GPS noise.

### Speed Profiles

| Mode | Speed (km/h) | Typical leg duration |
|------|-------------|---------------------|
| Walking | 4-6 | 5-8 min |
| Waiting | 0-0.5 | 1-3 min |
| Driving | 25-50 | 15-25 min |
| Train | 50-90 | 15-25 min |

### Variability

- Morning departure: ~7:30 AM ET +/- 15 min random offset per day
- Evening departure: ~5:30 PM ET +/- 15 min random offset per day
- Each leg duration has ~10% random variance
- Weekdays only (Mon-Fri), skipping weekends

### Data Volume

Default: 20 weekdays (~40 commutes, ~2,400-4,000 GPS points total). Configurable via `--days`.

## Label Corrections

~8 label corrections inserted across different commutes to populate `segment_labels`:

- 2-3 "stationary" -> "waiting" (platform waits misclassified as stationary)
- 2-3 "driving" -> "train" (speed overlap zone misclassifications)
- 1-2 "driving" -> "walking" (slow driving near station classified wrong)

Corrections target segments from the first ~5 commutes so there's labeled data available immediately.

## Script Interface

```
python scripts/seed_dev_data.py              # 20 weekdays
python scripts/seed_dev_data.py --days 5     # 1 week
python scripts/seed_dev_data.py --days 60    # 3 months
python scripts/seed_dev_data.py --clean      # Wipe DB + derived before seeding
```

### What the script does (in order)

1. Optionally wipe existing DB records and derived Parquet files (`--clean`)
2. Generate synthetic GPS payloads for N weekdays
3. Insert each payload into the database via `Database.insert_record()`
4. Run `process_from_db()` to generate derived Parquet files
5. Insert label corrections into `segment_labels`
6. Print summary: records inserted, commutes detected, files written, labels added
7. Print the geofence env vars needed in `.env`

### Exit behavior

- If the database already has records and `--clean` is not set, warn and ask for confirmation (or add `--force` to skip)

## Out of Scope

- Docker-specific logic (script works against whatever DATABASE_URL is configured)
- S3 sync setup
- ML model training (user can run `scripts/train_model.py` separately after seeding)

## File Changes

| File | Change |
|------|--------|
| `scripts/seed_dev_data.py` | New — the seed script |
| `docs/local-development.md` | Add seed data section |
