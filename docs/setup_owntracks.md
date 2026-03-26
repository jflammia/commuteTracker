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
```
locatorDisplacement: 10       # 10 meters (more granular than default 100m)
locatorInterval: 10           # 10 seconds (more frequent than default 300s)
monitoring: 2                 # Move Mode
downgrade: 20                 # Fall back to Significant at 20% battery
ignoreInaccurateLocations: 50 # Drop fixes worse than 50m accuracy
```

---

## Data Queuing & Durability

- **Server unreachable**: OwnTracks queues messages on-device and retries on next publish
- **Queue is in-memory only**: If you force-quit the app or it crashes, queued messages are LOST
- **Changing modes or endpoints clears the queue**
- **Recommendation**: Keep the receiver server running reliably to minimize queued messages

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
- If server is unreachable, messages are queued (in-memory) for later delivery
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

## Data Loss Risks

1. **Force-quitting the app** clears in-memory queue of unsent messages
2. **App crashes** can reset the queue
3. **Changing modes** clears queued messages
4. **Changing endpoints** clears queued messages
5. **iOS killing the app** in extreme memory pressure

**Mitigation**: Keep receiver server running. Don't force-quit OwnTracks. Don't
change modes/endpoints while offline.
