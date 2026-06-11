<script lang="ts">
  import { getTrips, type TripSummary } from '$lib/api';

  let { data } = $props();
  let trips: TripSummary[] = $state(data.trips);
  let unreviewedOnly = $state(false);

  async function toggleFilter() {
    unreviewedOnly = !unreviewedOnly;
    trips = await getTrips(fetch, unreviewedOnly ? false : undefined);
  }

  function fmtTime(iso: string): string {
    return new Date(iso).toLocaleString();
  }
  function fmtKm(m: number): string {
    return (m / 1000).toFixed(1) + ' km';
  }
  function fmtMin(s: number): string {
    return Math.round(s / 60) + ' min';
  }
</script>

<h1>Trips</h1>
<label class="filter">
  <input type="checkbox" checked={unreviewedOnly} onchange={toggleFilter} />
  Unreviewed only
</label>

{#if trips.length === 0}
  <p>No trips{unreviewedOnly ? ' awaiting review' : ' yet'}.</p>
{:else}
  <table>
    <thead>
      <tr><th>Start</th><th>Direction</th><th>Duration</th><th>Distance</th><th>Status</th></tr>
    </thead>
    <tbody>
      {#each trips as t (t.trip_id)}
        <tr>
          <td><a href="/trips/{t.trip_id}">{fmtTime(t.start_ts)}</a></td>
          <td>{t.direction}</td>
          <td>{fmtMin(t.duration_s)}</td>
          <td>{fmtKm(t.distance_m)}</td>
          <td>{t.reviewed ? '✓ reviewed' : '· unreviewed'}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e0e0e0; }
  .filter { display: inline-flex; gap: 0.4rem; align-items: center; margin-bottom: 1rem; }
</style>
