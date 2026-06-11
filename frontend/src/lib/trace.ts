// Pure GeoJSON construction — testable without a map.

export const MODE_COLORS: Record<string, string> = {
  walk: '#2e7d32',
  vehicle: '#1565c0',
  train: '#6a1b9a',
  stationary: '#9e9e9e',
  fallback: '#e65100',
};

export interface TracePoint {
  ts: string;
  lat: number;
  lon: number;
}

export interface TraceSegment {
  seg_index: number;
  mode_effective: string;
  start_ts: string;
  end_ts: string;
}

export function buildSegmentFeatures(points: TracePoint[], segments: TraceSegment[]) {
  return segments.map((seg) => {
    // ISO-8601 strings with identical offsets compare lexicographically
    const coords = points
      .filter((p) => p.ts >= seg.start_ts && p.ts <= seg.end_ts)
      .map((p) => [p.lon, p.lat]);
    return {
      type: 'Feature' as const,
      properties: {
        seg_index: seg.seg_index,
        mode: seg.mode_effective,
        color: MODE_COLORS[seg.mode_effective] ?? MODE_COLORS.fallback,
      },
      geometry: { type: 'LineString' as const, coordinates: coords },
    };
  });
}
