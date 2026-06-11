<script lang="ts">
  import maplibregl from 'maplibre-gl';
  import 'maplibre-gl/dist/maplibre-gl.css';
  import { onMount } from 'svelte';
  import { buildSegmentFeatures, type TracePoint, type TraceSegment } from './trace';

  let { points, segments }: { points: TracePoint[]; segments: TraceSegment[] } = $props();
  let container: HTMLDivElement;

  onMount(() => {
    const features = buildSegmentFeatures(points, segments);
    const lats = points.map((p) => p.lat);
    const lons = points.map((p) => p.lon);
    const bounds: [[number, number], [number, number]] = [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ];
    const map = new maplibregl.Map({
      container,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap contributors',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
      bounds,
      fitBoundsOptions: { padding: 48 },
      attributionControl: { compact: true },
    });
    map.on('load', () => {
      map.addSource('trace', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features },
      });
      map.addLayer({
        id: 'trace',
        type: 'line',
        source: 'trace',
        paint: { 'line-color': ['get', 'color'], 'line-width': 4 },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      });
    });
    return () => map.remove();
  });
</script>

<div bind:this={container} class="map" data-testid="trip-map"></div>

<style>
  .map { height: 420px; width: 100%; border-radius: 8px; }
</style>
