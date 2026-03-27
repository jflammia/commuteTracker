# MCP Integration Guide

The Commute Tracker includes a built-in [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server. Connect Claude (or any MCP client) and it can read your commute data, review classifications, apply corrections, and trigger reprocessing — no scripting needed.

## Connect in 30 Seconds

You need one thing: the URL of your running Commute Tracker instance with `/mcp/` appended.

**Examples:**
- Local: `http://localhost:8080/mcp/`
- Homelab: `http://192.168.1.50:8080/mcp/`
- Behind reverse proxy: `https://commute.example.com/mcp/`

### Claude Code

If you cloned the repo, it just works — `.mcp.json` auto-connects to `localhost:8080`. For a remote server, edit `.mcp.json` in the repo root:

```json
{
  "mcpServers": {
    "commute-tracker": {
      "type": "http",
      "url": "https://your-server/mcp/"
    }
  }
}
```

### Claude Desktop

Open **Settings > Developer > Edit Config** and add:

```json
{
  "mcpServers": {
    "commute-tracker": {
      "type": "http",
      "url": "https://your-server/mcp/"
    }
  }
}
```

### Any Other MCP Client

The server uses Streamable HTTP transport (stateless, JSON responses). Point your client at the `/mcp/` URL — no API key, no auth headers, no session management required.

| Property   | Value                                      |
|------------|--------------------------------------------|
| Transport  | Streamable HTTP (MCP spec 2025-03-26)      |
| Auth       | None (use network-level access control)    |
| Stateless  | Yes — no session state between requests    |
| Response   | JSON                                       |

### Verify the Connection

Ask Claude: *"What commute data do you have access to?"*

If connected, Claude will read the `commutes://list` resource and tell you about your commutes. If not, it will say it can't access the data.

---

## Resources (Read-Only Data)

Resources are analogous to GET endpoints. They provide read-only access to commute data. Use `read_resource` with the URI to fetch data.

| URI | Description |
|-----|-------------|
| `commutes://list` | All commutes with summary stats |
| `commutes://{commute_id}` | Full commute: points, segments, labels |
| `commutes://{commute_id}/segments` | Segment breakdown for a commute |
| `commutes://{commute_id}/points` | All GPS points for a commute |
| `stats://overview` | Aggregate stats by direction |
| `stats://raw` | Raw GPS record counts |
| `stats://health` | System health and status |
| `daily://{day}` | All points for a date (YYYY-MM-DD) |
| `dates://list` | Available processed dates |
| `labels://list` | All user label corrections |
| `labels://{commute_id}` | Labels for a specific commute |
| `labels://corrections` | Correction lookup map |

### Example: Read a Commute

```
read_resource("commutes://2026-03-26-morning")
```

Returns JSON with `commute_id`, `points` array, `segments` array, and `labels` array.

---

## Tools (Actions)

Tools perform actions — queries, corrections, processing. They are the primary interface for LLM labeling workflows.

### Data Query

#### `query_commute_data`

Run SQL over all processed data via DuckDB.

| Parameter | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `sql`     | string | yes      | SQL using `commute_data` as table |

**Available columns:** `lat`, `lon`, `speed_kmh`, `timestamp`, `distance_m`, `time_delta_s`, `is_stationary`, `commute_id`, `commute_direction`, `transport_mode`, `segment_id`.

```
query_commute_data(sql="SELECT commute_id, avg(speed_kmh) FROM commute_data GROUP BY commute_id")
```

#### `count_raw_records`

Preview how many raw records match filters before rebuilding.

| Parameter | Type   | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `since`   | string | no       | null    | Start date (YYYY-MM-DD) |
| `until`   | string | no       | null    | End date (YYYY-MM-DD) |
| `user`    | string | no       | null    | OwnTracks user |
| `device`  | string | no       | null    | OwnTracks device |

### Labeling — Individual

#### `add_segment_label`

Correct a single segment's transport mode.

| Parameter       | Type   | Required | Description |
|----------------|--------|----------|-------------|
| `commute_id`    | string | yes      | Commute identifier |
| `segment_id`    | int    | yes      | Segment number (0-indexed) |
| `original_mode` | string | yes      | Classifier-assigned mode |
| `corrected_mode`| string | yes      | Correct mode |
| `notes`         | string | no       | Explanation for correction |

**Valid modes:** `stationary`, `waiting`, `walking`, `driving`, `train`

#### `add_segment_labels_bulk`

Apply multiple corrections at once. Takes a `labels` array where each item has the same fields as `add_segment_label`.

### Labeling — Intelligent Review

These tools implement multi-level classification review. Use them to efficiently find and fix misclassifications at the right granularity.

#### `analyze_segment` (Low-Level)

Deep-dive into one segment. Returns speed stats (mean, median, max, min, std), duration, distance, neighboring segment modes, and mismatch detection.

| Parameter    | Type   | Required | Description |
|-------------|--------|----------|-------------|
| `commute_id` | string | yes      | Commute identifier |
| `segment_id` | int    | yes      | Segment number |

**Best for:** Investigating a specific suspicious segment before correcting it.

**Response includes:**
- `speed_mean_kmh`, `speed_median_kmh`, `speed_max_kmh`, `speed_min_kmh`, `speed_std_kmh`
- `prev_segment_mode`, `next_segment_mode` (context)
- `mismatch` (bool), `confidence` (0-1), `suggested_mode`, `reason`

#### `review_commute_labels` (Mid-Level)

Review all segments in a commute. Flags suspicious ones with confidence scores.

| Parameter    | Type   | Required | Description |
|-------------|--------|----------|-------------|
| `commute_id` | string | yes      | Commute identifier |

**Best for:** Reviewing a commute after processing. Finds all issues at once.

**Response includes:**
- `flagged_segments` — sorted by confidence (highest first)
- `suggested_corrections` — ready to pass to `apply_label_corrections`
- `all_segments` — every segment with its analysis

#### `review_recent_labels` (High-Level)

Batch review across multiple commutes. Finds systematic patterns.

| Parameter   | Type   | Required | Default | Description |
|------------|--------|----------|---------|-------------|
| `n`         | int    | no       | 5       | Commutes to review |
| `direction` | string | no       | null    | Filter by direction |

**Best for:** Initial quality audit, finding classifier weaknesses, batch fixes.

**Response includes:**
- `systematic_patterns` — recurring misclassification types with counts
- `suggested_corrections` — all corrections across reviewed commutes
- `commute_summaries` — overview per commute

#### `apply_label_corrections`

Apply corrections from a review, filtered by confidence.

| Parameter        | Type       | Required | Default | Description |
|-----------------|------------|----------|---------|-------------|
| `corrections`    | list[dict] | yes      | —       | From review's `suggested_corrections` |
| `min_confidence` | float      | no       | 0.7     | Minimum confidence (0.0-1.0) |

### Processing

#### `rebuild_derived_data`

Re-run the full pipeline from raw GPS data.

| Parameter | Type   | Required | Default | Description |
|-----------|--------|----------|---------|-------------|
| `since`   | string | no       | null    | Start date |
| `until`   | string | no       | null    | End date |
| `user`    | string | no       | null    | OwnTracks user |
| `device`  | string | no       | null    | OwnTracks device |
| `clean`   | bool   | no       | false   | Delete existing files first |
| `dry_run` | bool   | no       | false   | Preview only |

### ML

#### `train_ml_model`

Train the transport mode classifier from labeled segments.

| Parameter       | Type  | Required | Default | Description |
|----------------|-------|----------|---------|-------------|
| `max_depth`     | int   | no       | 10      | Decision tree depth |
| `test_fraction` | float | no       | 0.2     | Test holdout fraction |

#### `evaluate_classifier`

Compare classifier output to user labels. No parameters.

---

## Prompts (Templates)

Prompts are pre-built instruction templates that guide the LLM through common workflows. Invoke them to get structured analysis instructions.

| Prompt | Parameters | Description |
|--------|-----------|-------------|
| `analyze_commute` | `commute_id` | Step-by-step commute analysis with anomaly detection |
| `optimize_departure` | — | Find optimal departure time from historical data |
| `review_classifications` | — | Systematic review of recent segment classifications |
| `weekly_report` | — | Weekly commute summary with trends and recommendations |

### Example: Analyze a Commute

```
use_prompt("analyze_commute", commute_id="2026-03-26-morning")
```

The prompt instructs the LLM to:
1. Read the commute data
2. Examine each segment's speed, duration, and distance
3. Flag unusual patterns or possible misclassifications
4. Compare against historical averages
5. Provide actionable recommendations

---

## LLM Labeling Workflow

The recommended workflow for an LLM to review and correct classifications:

### Quick Review (Single Commute)

```
1. review_commute_labels(commute_id="2026-03-26-morning")
2. Inspect flagged_segments — do the suggestions look right?
3. apply_label_corrections(corrections=suggested_corrections, min_confidence=0.8)
```

### Deep Investigation (Single Segment)

```
1. analyze_segment(commute_id="...", segment_id=2)
2. Review speed stats and context
3. add_segment_label(commute_id="...", segment_id=2,
                     original_mode="driving", corrected_mode="train",
                     notes="avg speed 42 km/h with low variance = train")
```

### Batch Audit (Multiple Commutes)

```
1. review_recent_labels(n=10)
2. Check systematic_patterns for recurring issues
3. apply_label_corrections(corrections=suggested_corrections, min_confidence=0.7)
4. rebuild_derived_data(since="2026-03-20", clean=true)
5. train_ml_model()
```

### Full Quality Loop

```
1. review_recent_labels(n=20)           # Find problems
2. apply_label_corrections(...)          # Fix them
3. rebuild_derived_data(clean=true)      # Reprocess
4. train_ml_model()                      # Improve classifier
5. evaluate_classifier()                 # Measure improvement
6. review_recent_labels(n=20)            # Verify fewer flags
```

---

## Speed Thresholds

The mismatch detection engine uses these expected speed ranges (matching the SpeedClassifier):

| Mode       | Speed Range (km/h) |
|------------|---------------------|
| stationary | 0 – 1               |
| waiting    | 0 – 2               |
| walking    | 1 – 7               |
| driving    | 7 – 80              |
| train      | 30 – 300            |

Confidence scores (0–1) increase with the distance between observed and expected speed.

---

## SDK Compatibility

This server uses the MCP Python SDK v1.26+ with:
- **FastMCP** high-level API
- **Streamable HTTP** transport (spec revision 2025-03-26)
- **Stateless mode** — no session persistence between requests
- **JSON responses** — all tool and resource outputs are JSON strings

Compatible with any MCP client supporting Streamable HTTP: Claude Desktop, Claude Code, OpenAI Agents SDK, or custom clients using the MCP TypeScript/Python SDKs.
