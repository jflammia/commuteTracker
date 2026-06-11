<script lang="ts">
  import { getOptimizer, type OptimizerResult } from '$lib/api';
  import FanChart from '$lib/FanChart.svelte';
  import ItineraryCard from '$lib/ItineraryCard.svelte';

  let { data } = $props();
  let date = $state(data.today);
  let arriveBy = $state('09:00');
  let result: OptimizerResult | null = $state(null);
  let error = $state('');
  let loading = $state(false);

  async function run() {
    loading = true;
    error = '';
    try {
      result = await getOptimizer(fetch, date, arriveBy);
    } catch (e) {
      error = e instanceof Error && e.message === 'optimizer-unconfigured'
        ? 'The optimizer is not configured (set the commute stations in the backend).'
        : 'Could not compute itineraries.';
      result = null;
    } finally {
      loading = false;
    }
  }
</script>

<h1>Optimizer</h1>
<form class="goal" onsubmit={(e) => { e.preventDefault(); run(); }}>
  <label>Date <input type="date" bind:value={date} /></label>
  <label>Arrive by <input type="time" bind:value={arriveBy} /></label>
  <button type="submit" disabled={loading}>{loading ? 'Computing…' : 'Find trains'}</button>
</form>

{#if error}<p class="error">{error}</p>{/if}

{#if result}
  {#if result.options.length === 0}
    <p>No trains arrive in time for that goal.</p>
  {:else}
    <FanChart options={result.options} />
    {#each result.options as opt, i (opt.gtfs_trip_id)}
      <ItineraryCard option={opt} best={i === 0} />
    {/each}
  {/if}
{/if}

<style>
  .goal { display: flex; gap: 1rem; align-items: end; margin-bottom: 1rem; }
  .goal label { display: flex; flex-direction: column; gap: 0.25rem; }
  .error { color: #c62828; }
</style>
