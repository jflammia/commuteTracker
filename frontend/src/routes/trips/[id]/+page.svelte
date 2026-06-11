<script lang="ts">
  import Map from '$lib/Map.svelte';
  import SegmentPanel from '$lib/SegmentPanel.svelte';
  import { getTrip, type TripDetail } from '$lib/api';

  let { data } = $props();
  let detail: TripDetail = $state(data.detail);

  async function refresh() {
    detail = await getTrip(fetch, detail.trip.trip_id);
  }

  function fmtTime(iso: string): string {
    return new Date(iso).toLocaleString();
  }
</script>

<a href="/trips">← Trips</a>
<h1>
  {fmtTime(detail.trip.start_ts)}
  <small>{detail.trip.direction}</small>
  {#if detail.trip.flag === 'phantom'}<span class="flag">phantom</span>{/if}
  {#if detail.trip.reviewed}<span class="reviewed">✓ reviewed</span>{/if}
</h1>

{#key detail.trip.trip_id}
  <Map points={detail.points} segments={detail.segments} />
{/key}

<SegmentPanel {detail} onchange={refresh} />

<style>
  h1 small { color: #777; font-weight: 400; margin-left: 0.5rem; }
  .flag { background: #ffebee; color: #c62828; border-radius: 4px; padding: 0.1rem 0.5rem; font-size: 0.9rem; margin-left: 0.5rem; }
  .reviewed { background: #e8f5e9; color: #2e7d32; border-radius: 4px; padding: 0.1rem 0.5rem; font-size: 0.9rem; margin-left: 0.5rem; }
</style>
