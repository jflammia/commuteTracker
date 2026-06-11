import { describe, expect, it } from 'vitest';
import { buildSegmentFeatures, MODE_COLORS } from './trace';

const points = [
  { ts: '2026-06-10T14:00:00+00:00', lat: 40.7, lon: -74.4 },
  { ts: '2026-06-10T14:01:00+00:00', lat: 40.71, lon: -74.4 },
  { ts: '2026-06-10T14:02:00+00:00', lat: 40.72, lon: -74.4 },
  { ts: '2026-06-10T14:03:00+00:00', lat: 40.73, lon: -74.4 },
];
const segments = [
  { seg_index: 0, mode_effective: 'walk', start_ts: '2026-06-10T14:00:00+00:00', end_ts: '2026-06-10T14:01:00+00:00' },
  { seg_index: 1, mode_effective: 'vehicle', start_ts: '2026-06-10T14:01:00+00:00', end_ts: '2026-06-10T14:03:00+00:00' },
];

describe('buildSegmentFeatures', () => {
  it('builds one feature per segment with mode colors', () => {
    const features = buildSegmentFeatures(points, segments);
    expect(features).toHaveLength(2);
    expect(features[0].properties.color).toBe(MODE_COLORS.walk);
    expect(features[1].properties.color).toBe(MODE_COLORS.vehicle);
    expect(features[0].geometry.coordinates).toEqual([
      [-74.4, 40.7],
      [-74.4, 40.71],
    ]);
  });

  it('shares boundary points between adjacent segments', () => {
    const features = buildSegmentFeatures(points, segments);
    expect(features[1].geometry.coordinates[0]).toEqual([-74.4, 40.71]);
  });

  it('handles unknown modes with a fallback color', () => {
    const features = buildSegmentFeatures(points, [
      { ...segments[0], mode_effective: 'mystery' },
    ]);
    expect(features[0].properties.color).toBe(MODE_COLORS.fallback);
  });
});
