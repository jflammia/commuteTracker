# OwnTracks Setup Guide (iOS)

## Overview

OwnTracks is a free, open-source location tracking app. We use **HTTP mode** to POST
GPS data from the iPhone to our FastAPI receiver running on the homelab.

---

## iOS Configuration Checklist

### System Settings (iOS Settings App)
1. **Privacy & Security > Location Services > OwnTracks** -> **Always**
2. **General > Background App Refresh** -> On for OwnTracks
3. **Battery > Low Power Mode** -> Off during commute (degrades tracking)

### OwnTracks App Settings
1. **Mode**: HTTP
2. **URL**: `https://<your-receiver-host>:8080/pub` (or Tailscale IP)
3. **Identification**: Set username and device name (required for HTTP mode)
4. **Monitoring**: Move Mode (for commute tracking)

---

## Tracking Modes

| Mode | Symbol | Behavior | Frequency | Battery |
|------|--------|----------|-----------|---------|
| **Quiet** | `[]` | Manual reports only, no region events | Never | Minimal |
| **Manual** | `\|\|` | Manual reports + region monitoring | On demand | Low |
| **Significant** | `\|>` | iOS significant location change | ~500m AND ~5 min | Very low |
| **Move** | `\|\|>` | Continuous GPS monitoring | Every `locatorInterval` sec OR `locatorDisplacement` meters | High (like nav apps) |

### Move Mode (recommended for commuting)
- Publishes a new location when the device moves `locatorDisplacement` meters (default: 100m)
  OR after `locatorInterval` seconds (default: 300s), **whichever comes first**
- Battery usage comparable to running a navigation app
- Can auto-downgrade to Significant mode when battery drops below `downgrade` threshold

### Significant Location Change Mode (for non-commute hours)
- iOS system-level feature: reports only when moving >500m in >5 minutes
- Uses cell towers and WiFi (not GPS) -- very battery efficient
- May produce no updates if you're stationary or moving short distances

---

## Key Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `locatorDisplacement` | 100 | Meters before triggering publish in Move Mode |
| `locatorInterval` | 300 | Seconds before triggering publish in Move Mode |
| `monitoring` | 1 | Mode: -1=quiet, 0=manual, 1=significant, 2=move |
| `downgrade` | - | Battery % to auto-switch Move -> Significant |
| `adapt` | 0 | Minutes of non-movement before auto-switch to Significant (0=disabled) |
| `ignoreInaccurateLocations` | - | Suppress fixes worse than N meters accuracy |
| `ignoreStaleLocations` | 0 | Filter locations older than N days |
| `days` | -1 | Days of local location retention |

### Recommended Settings for Commute Tracking

These settings are tuned for reliable commute detection with minimal battery impact. Configure once and forget.

| Field Label (exact) | Value | Units | Purpose |
|---------------------|-------|-------|---------|
| `monitoring` | `2` | mode | **Move mode** — continuous background tracking |
| `locatorInterval` | `30` | seconds | Report location every 30s while moving |
| `locatorDisplacement` | `50` | meters | Only report when you've moved 50m+ |
| `ignoreInaccurateLocations` | `100` | meters | Drop GPS readings with >100m accuracy |
| `ignoreStaleLocations` | `1` | days | Discard cached location data older than 1 day |

#### Why these values?

**`monitoring` = 2 (Move mode)** — Significant mode (1) only triggers on ~500m movements, missing short walking segments (e.g., the 3-minute walk from train station to office). Move mode captures these transitions reliably.

**`locatorInterval` = 30 seconds** — The transport mode classifier detects transitions by watching speed changes over time. Walking-to-train transitions happen over 2-5 minutes, so 30-second intervals provide 4-10 data points per transition — plenty for accurate classification without excessive battery drain.

**`locatorDisplacement` = 50 meters** — The key battery-saving setting. While sitting at home or at your desk, the app won't send reports because you haven't moved 50 meters. Without this, a work-from-home day generates thousands of identical stationary points. With it, you get near-zero points while stationary and full coverage while commuting.

**`ignoreInaccurateLocations` = 100 meters** — Indoor GPS can produce readings with 200-500m accuracy. These noisy fixes create phantom movement that confuses the classifier. Filtering at 100m ensures only reliable outdoor GPS data reaches CommuteTracker.

**`ignoreStaleLocations` = 1 day** — Safety net that discards any cached or delayed location data. Prevents old location batches from being misinterpreted as current movement.

#### Step-by-step

1. Open OwnTracks on your iPhone
2. Tap the **gear icon** to open Settings
3. Set each field listed above to the recommended value
4. Return to the main map view — tracking begins immediately
5. You should never need to revisit these settings

---

## Data Queuing & Durability (from source code analysis)

**Good news**: In HTTP mode, the queue is **CoreData-backed (disk-persisted)**, not in-memory
as the documentation suggests. Messages survive app restarts and crashes.

- **Server unreachable**: Messages are queued in CoreData and retried with exponential backoff
  (1s, 2s, 4s... up to 64s between retries)
- **Queue limits**: Max 100,000 messages or 100MB. Oldest messages dropped if exceeded.
- **HTTP 4xx errors**: Messages are **discarded** (treated as permanent failure). Our receiver
  must return 2xx for all valid payloads, even if processing fails.
- **HTTP 5xx / network errors**: Messages are preserved and retried.
- **Recommendation**: Keep the receiver server running and always return 200. Never return 4xx
  for valid location payloads.

---

## Background Behavior (iOS)

- The app tracks location in the background without needing to be open
- When backgrounded, TCP connections drop (this is why HTTP mode > MQTT for us)
- Background reconnection attempts happen roughly every 10 minutes
- Region monitoring (geofences) works independently of tracking mode
- iOS may suspend the app; it wakes up on significant location changes

---

## What You See in the App

### Map View
- Your location shown as a pin/marker
- Friends' locations (if configured) with profile pics or 2-char tracker IDs
- Velocity indicator: red/yellow tachometer ring (7 o'clock = 0, 12 = 130 km/h, 5 = 260 km/h)
- Course direction: small blue/yellow semicircle (12 o'clock = North)
- Monitored regions: blue circles on map, turning reddish when inside

### Status/Info Screen
- Connection status
- Message queue count
- Export functionality (GPX format)

---

## Trigger Types in Location Payloads

The `t` field in each location message indicates why it was published:

| Trigger | Meaning |
|---------|---------|
| `p` | Ping (periodic) |
| `c` | Circular region event |
| `C` | Follow region (dynamic) |
| `b` | Beacon event |
| `r` | Response to remote command |
| `u` | Manual (user triggered) |
| `t` | Timer-based |
| `v` | Move mode (frequent locations) |

---

## Location Payload Fields (complete)

| Field | Type | Description |
|-------|------|-------------|
| `_type` | string | Always "location" |
| `lat` | float | Latitude (required) |
| `lon` | float | Longitude (required) |
| `tst` | integer | UNIX epoch timestamp of fix (required) |
| `acc` | integer | Horizontal accuracy (meters) |
| `alt` | integer | Altitude above sea level (meters) |
| `batt` | integer | Battery level (%) |
| `bs` | integer | Battery status: 0=unknown, 1=unplugged, 2=charging, 3=full |
| `cog` | integer | Course over ground (degrees, iOS only) |
| `vel` | integer | Velocity (km/h) |
| `vac` | integer | Vertical accuracy (meters, iOS only) |
| `p` | float | Barometric pressure (kPa, iOS only, extended data) |
| `conn` | string | Connectivity: w=WiFi, o=offline, m=mobile |
| `SSID` | string | WiFi network name (iOS only) |
| `BSSID` | string | WiFi access point ID (iOS only) |
| `t` | string | Trigger type (see table above) |
| `tid` | string | Tracker ID (2 chars, required for HTTP) |
| `m` | integer | Monitoring mode: 1=significant, 2=move (iOS only) |
| `rad` | integer | Region radius for enter/leave events (iOS only) |
| `poi` | string | Point of interest name (iOS only) |
| `tag` | string | Tag name (iOS only) |
| `topic` | string | MQTT-style topic (HTTP mode, iOS) |
| `inregions` | array | Current region names |
| `inrids` | array | Current region IDs |
| `created_at` | integer | Message construction timestamp |
| `motionactivities` | array | Motion states detected (iOS only) |

---

## HTTP Mode Details

- App POSTs JSON to configured URL with Content-Type: application/json
- Headers include `X-Limit-U` (username) and `X-Limit-D` (device name)
- Server should return 2xx with empty JSON array `[]` or array of command objects
- If server is unreachable, messages are queued in CoreData (disk-persisted) and retried
- Authentication via HTTP Basic (user:password in URL)
- TLS strongly recommended

---

## Smart Features for Commute Use

### +follow Regions
A region named `+follow` dynamically moves with you. Its radius auto-adjusts to
distance covered in 30 seconds (min 50m). Useful for the `adapt` setting.

### Auto Mode Switching via Regions
Name a region `Home|1|2` to auto-switch to Move Mode (2) when leaving home
and Significant Mode (1) when arriving. This saves battery outside commute hours.

### Battery Downgrade
Set `downgrade: 20` to auto-fall-back from Move to Significant when battery
drops below 20%. Plugging in the charger reverts to Move Mode automatically.

---

## Data Loss Risks (verified from source code)

In HTTP mode, the queue is CoreData-persisted, so the risks are less severe than
the documentation suggests. However:

1. **HTTP 4xx from receiver** - messages are permanently discarded (not retried).
   Our receiver MUST always return 2xx.
2. **Queue overflow** - if >100K messages or >100MB queue, oldest are dropped.
   This would require ~12 days of offline 10-second tracking.
3. **Changing endpoints** may clear the queue (needs verification)
4. **Location filtering** - the app silently drops locations with identical/older
   timestamps, or with 0.0,0.0 coordinates

**Mitigation**: Keep receiver running and always returning 200. The CoreData queue
provides much better durability than documented.
