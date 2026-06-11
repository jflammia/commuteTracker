<script lang="ts">
  import type { ItineraryOption } from './api';
  let { option, best }: { option: ItineraryOption; best: boolean } = $props();
  function clock(iso: string): string {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<div class="card" class:best data-testid="itinerary-{option.gtfs_trip_id}">
  {#if best}<span class="badge">recommended</span>{/if}
  <div class="leave">Leave by <strong>{clock(option.leave_by)}</strong></div>
  <div class="train">🚆 {option.route_name} · {option.gtfs_trip_id} → {option.headsign}</div>
  <div class="stops">{option.board_stop} → {option.alight_stop}</div>
  <div class="arrive">
    Arrive {clock(option.p50_arrive)} <span class="p90">(P90 {clock(option.p90_arrive)})</span>
  </div>
</div>

<style>
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 0.75rem 1rem; margin: 0.5rem 0; }
  .card.best { border-color: #1565c0; background: #f3f8ff; }
  .badge { background: #1565c0; color: #fff; font-size: 0.75rem; border-radius: 4px;
           padding: 0.1rem 0.5rem; }
  .leave { font-size: 1.1rem; margin: 0.25rem 0; }
  .train { color: #333; }
  .stops { color: #777; font-size: 0.9rem; }
  .p90 { color: #999; }
</style>
