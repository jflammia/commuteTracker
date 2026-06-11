<!-- frontend/src/routes/today/+page.svelte -->
<script lang="ts">
  import ItineraryCard from '$lib/ItineraryCard.svelte';

  let { data } = $props();
</script>

<h1>Today</h1>

{#if !data.configured}
  <p class="muted">
    The optimizer isn't configured yet. Set your commute stations and target arrival
    time in the backend to see a daily recommendation here.
  </p>
{:else if data.rec && data.rec.options.length > 0}
  <p class="goal">Get to {data.rec.options[0].alight_stop} by {data.rec.arrive_by_local}:</p>
  <ItineraryCard option={data.rec.options[0]} best={true} />
  {#if data.rec.options.length > 1}
    <h2>Alternatives</h2>
    {#each data.rec.options.slice(1, 4) as opt (opt.gtfs_trip_id)}
      <ItineraryCard option={opt} best={false} />
    {/each}
  {/if}
  <p class="hint"><a href="/optimizer">Try a different arrival time →</a></p>
{:else}
  <p class="muted">No trains arrive in time for your target today.</p>
{/if}

<style>
  .goal { font-size: 1.1rem; }
  .muted { color: #777; }
  .hint { margin-top: 1.5rem; }
</style>
