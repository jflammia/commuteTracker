# REST API Reference

Base URL: `http://localhost:8080/api/v1`

Interactive docs (Swagger UI) are available at `http://localhost:8080/docs` when the server is running.
OpenAPI spec (JSON) is at `http://localhost:8080/openapi.json`.

## Authentication

None. The API is designed for local/private network use. Restrict access at the network level (firewall, Tailscale, reverse proxy).

## Transport Modes

All mode fields accept one of: `stationary`, `waiting`, `walking`, `driving`, `train`.

---

## System

### `GET /health`

System health check.

**Response:**

```json
{
  "status": "ok",
  "total_records": 1423,
  "unsynced_records": 0,
  "label_count": 12,
  "derived_dates": ["2026-03-24", "2026-03-25", "2026-03-26"]
}
```

---

## Commutes

### `GET /commutes`

List all detected commutes with summary statistics.

**Response:** Array of commute summaries.

```json
[
  {
    "commute_id": "2026-03-26-morning",
    "commute_direction": "to_work",
    "start_time": "2026-03-26T07:15:00",
    "end_time": "2026-03-26T08:05:00",
    "duration_min": 50.0,
    "total_distance_m": 32150.0,
    "point_count": 312
  }
]
```

### `GET /commutes/{commute_id}`

Get full commute details: all GPS points, segments, and labels.

**Response:**

```json
{
  "commute_id": "2026-03-26-morning",
  "points": [ ... ],
  "segments": [ ... ],
  "labels": [ ... ]
}
```

### `GET /commutes/{commute_id}/segments`

Get the segment breakdown for a commute.

**Response:**

```json
[
  {
    "segment_id": 0,
    "transport_mode": "walking",
    "start_time": "2026-03-26T07:15:00",
    "end_time": "2026-03-26T07:22:00",
    "duration_min": 7.0,
    "distance_m": 580.0,
    "avg_speed_kmh": 5.0,
    "max_speed_kmh": 6.2,
    "point_count": 42
  }
]
```

### `GET /commutes/{commute_id}/points`

Get all GPS points for a commute, ordered chronologically.

**Response:** Array of point objects with `lat`, `lon`, `speed_kmh`, `timestamp`, `transport_mode`, `segment_id`, `distance_m`, `time_delta_s`.

---

## Analytics

### `GET /stats`

Aggregate statistics across all commutes, broken down by direction.

**Response:**

```json
{
  "rows": [
    {
      "commute_direction": "to_work",
      "num_commutes": 15,
      "avg_duration_min": 48.3,
      "min_duration_min": 38.0,
      "max_duration_min": 65.0,
      "stddev_duration_min": 7.2
    }
  ]
}
```

### `GET /daily/{day}`

Get all processed data points for a specific date.

| Parameter | Type   | Description              |
|-----------|--------|--------------------------|
| `day`     | string | Date in `YYYY-MM-DD` format |

### `POST /query`

Run a SQL query over derived Parquet data via DuckDB.

**Request:**

```json
{
  "sql": "SELECT commute_id, avg(speed_kmh) as avg_speed FROM commute_data GROUP BY commute_id"
}
```

Use `commute_data` as the table name. Available columns: `lat`, `lon`, `speed_kmh`, `timestamp`, `distance_m`, `time_delta_s`, `is_stationary`, `commute_id`, `commute_direction`, `transport_mode`, `segment_id`.

### `GET /raw/stats`

Raw GPS data statistics: total and unsynced record counts.

### `GET /raw/count`

Count raw records matching filters. Useful for previewing a rebuild.

| Parameter | Type   | Description                    |
|-----------|--------|--------------------------------|
| `since`   | string | Start date inclusive (YYYY-MM-DD) |
| `until`   | string | End date inclusive (YYYY-MM-DD)   |
| `user`    | string | OwnTracks user filter          |
| `device`  | string | OwnTracks device filter        |

### `GET /dates`

List all dates that have processed Parquet data.

**Response:** `["2026-03-24", "2026-03-25", "2026-03-26"]`

---

## Labels

Segment labels are user corrections to the automatic transport mode classifier. They persist in the database and are applied when derived data is rebuilt. Labels also serve as training data for the ML model.

### `GET /labels`

List all labels, optionally filtered by commute.

| Parameter    | Type   | Description           |
|--------------|--------|-----------------------|
| `commute_id` | string | Filter by commute ID |

**Response:**

```json
[
  {
    "commute_id": "2026-03-26-morning",
    "segment_id": 2,
    "original_mode": "driving",
    "corrected_mode": "train",
    "notes": "Classifier confused by tunnel GPS jitter",
    "labeled_at": "2026-03-26T12:00:00"
  }
]
```

### `POST /labels`

Add or update a single segment label.

**Request:**

```json
{
  "commute_id": "2026-03-26-morning",
  "segment_id": 2,
  "original_mode": "driving",
  "corrected_mode": "train",
  "notes": "Classifier confused by tunnel GPS jitter"
}
```

### `POST /labels/bulk`

Add multiple labels in one request. Body is an array of label objects (same schema as `POST /labels`).

### `GET /labels/corrections`

Flat correction lookup map for frontends.

**Response:**

```json
{
  "2026-03-26-morning:2": "train",
  "2026-03-26-morning:4": "waiting"
}
```

### `GET /labels/export`

Export all labels as a JSON document for backup or ML training.

---

## Label Intelligence

Three levels of automated classification review, plus an action endpoint to apply corrections. Designed for LLM integration: an LLM can pick the appropriate granularity level and then apply corrections via a single follow-up call.

### `GET /labels/analyze/{commute_id}/{segment_id}` (Low-Level)

Deep analysis of a single segment. Returns speed statistics, neighboring segment context, and mismatch detection with a suggested correction.

**Response:**

```json
{
  "commute_id": "2026-03-26-morning",
  "segment_id": 2,
  "classified_mode": "driving",
  "point_count": 45,
  "speed_mean_kmh": 42.3,
  "speed_median_kmh": 40.1,
  "speed_max_kmh": 55.0,
  "speed_min_kmh": 28.5,
  "speed_std_kmh": 8.2,
  "duration_min": 12.5,
  "distance_m": 8800.0,
  "prev_segment_mode": "waiting",
  "next_segment_mode": "walking",
  "mismatch": true,
  "confidence": 0.82,
  "suggested_mode": "train",
  "reason": "avg speed 42.3 km/h outside driving range (7-80), max 55.0 km/h"
}
```

### `GET /labels/review/{commute_id}` (Mid-Level)

Review all segments in a commute. Flags suspicious segments sorted by confidence.

**Response:**

```json
{
  "commute_id": "2026-03-26-morning",
  "total_segments": 7,
  "flagged_count": 2,
  "flagged_segments": [
    {
      "segment_id": 2,
      "classified_mode": "driving",
      "avg_speed_kmh": 42.3,
      "mismatch": true,
      "confidence": 0.82,
      "suggested_mode": "train",
      "reason": "avg speed 42.3 km/h outside driving range (7-80), max 55.0 km/h"
    }
  ],
  "all_segments": [ ... ],
  "suggested_corrections": [
    {
      "commute_id": "2026-03-26-morning",
      "segment_id": 2,
      "original_mode": "driving",
      "corrected_mode": "train",
      "confidence": 0.82,
      "notes": "auto-flagged: avg speed 42.3 km/h outside driving range"
    }
  ]
}
```

### `GET /labels/review` (High-Level)

Review recent commutes for systematic misclassification patterns.

| Parameter   | Type    | Default | Description                     |
|-------------|---------|--------|---------------------------------|
| `n`         | integer | 5      | Number of recent commutes (1-50) |
| `direction` | string  | null   | Filter (e.g. `to_work`)         |

**Response:**

```json
{
  "commutes_reviewed": 5,
  "commute_summaries": [
    {"commute_id": "2026-03-26-morning", "total_segments": 7, "flagged_count": 2}
  ],
  "total_flagged": 8,
  "systematic_patterns": [
    {
      "pattern": "driving -> train",
      "count": 4,
      "avg_speed_kmh": 45.2,
      "avg_confidence": 0.85,
      "commute_ids": ["2026-03-26-morning", "2026-03-25-morning"]
    }
  ],
  "suggested_corrections": [ ... ]
}
```

### `POST /labels/apply`

Apply corrections from a review, filtered by confidence threshold.

**Request:**

```json
{
  "corrections": [
    {
      "commute_id": "2026-03-26-morning",
      "segment_id": 2,
      "original_mode": "driving",
      "corrected_mode": "train",
      "confidence": 0.82,
      "notes": "auto-flagged"
    }
  ],
  "min_confidence": 0.7
}
```

**Response:**

```json
{
  "applied_count": 1,
  "skipped_count": 0,
  "min_confidence": 0.7,
  "applied": [ ... ],
  "skipped": [ ... ]
}
```

**Workflow:**

```
GET /labels/review  -->  inspect suggested_corrections  -->  POST /labels/apply
```

---

## Processing

### `POST /rebuild`

Rebuild derived Parquet files from raw GPS data.

**Request:**

```json
{
  "since": "2026-03-01",
  "until": "2026-03-31",
  "clean": true,
  "dry_run": false
}
```

**Response:**

```json
{
  "dry_run": false,
  "filters": {"since": "2026-03-01", "until": "2026-03-31"},
  "dates_processed": ["2026-03-24", "2026-03-25", "2026-03-26"],
  "files_written": 3
}
```

---

## ML

### `POST /ml/train`

Train the ML transport mode classifier from labeled data.

**Request (optional):**

```json
{
  "max_depth": 10,
  "test_fraction": 0.2
}
```

**Response:**

```json
{
  "accuracy": 0.87,
  "sample_count": 150,
  "per_class": {"walking": 0.92, "driving": 0.85, "train": 0.88},
  "feature_importances": {"speed_kmh": 0.35, "speed_std_5": 0.18}
}
```

### `GET /ml/evaluate`

Compare the ensemble classifier against user labels. Returns accuracy metrics by mode.

---

## Error Handling

All errors return standard HTTP status codes with a JSON body:

```json
{"detail": "Commute 2026-03-26-morning not found"}
```

| Code | Meaning                           |
|------|-----------------------------------|
| 200  | Success                           |
| 400  | Bad request (invalid SQL, etc.)   |
| 404  | Resource not found                |
| 422  | Validation error (invalid params) |
| 500  | Internal server error             |
