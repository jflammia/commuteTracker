<script lang="ts">
  import { postLabel, type TripDetail } from './api';
  import { MODE_COLORS } from './trace';

  let { detail, onchange }: { detail: TripDetail; onchange: () => void } = $props();
  let busy = $state(false);

  const MODES = ['stationary', 'walk', 'vehicle', 'train'];

  async function label(event: Parameters<typeof postLabel>[0]) {
    busy = true;
    try {
      await postLabel(event);
      onchange();
    } finally {
      busy = false;
    }
  }

  function fmtClock(iso: string): string {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  function fmtMin(s: number): string {
    return Math.round(s / 60) + ' min';
  }
  function fmtDelta(s: number): string {
    const m = Math.round(Math.abs(s) / 60);
    return s >= 0 ? `${m} min after sched` : `${m} min before sched`;
  }
</script>

<section class="segments" data-testid="segment-panel">
  <h2>Segments</h2>
  {#each detail.segments as seg, i (seg.seg_index)}
    {@const leg = detail.itinerary[i]}
    <div class="segment" data-testid="segment-{seg.seg_index}">
      <span class="swatch" style="background: {MODE_COLORS[seg.mode_effective] ?? MODE_COLORS.fallback}"></span>
      <span class="time">{fmtClock(seg.start_ts)}–{fmtClock(seg.end_ts)}</span>
      <span class="dur">{fmtMin(seg.duration_s)}</span>
      <select
        value={seg.mode_effective}
        disabled={busy}
        data-testid="mode-select-{seg.seg_index}"
        onchange={(e) =>
          label({
            type: 'segment_mode',
            trip_id: detail.trip.trip_id,
            seg_index: seg.seg_index,
            value: e.currentTarget.value,
          })}
      >
        {#each MODES as m (m)}
          <option value={m}>{m}{m === seg.mode ? ' (auto)' : ''}</option>
        {/each}
      </select>
      {#if seg.mode_source === 'label'}<span class="labeled">labeled</span>{/if}

      {#if leg?.train}
        <div class="train">
          🚆 {leg.train.route_name} → {leg.train.headsign}
          ({leg.train.board_stop} → {leg.train.alight_stop}, {fmtDelta(leg.train.delta_s)})
          {#if leg.confirmation === 'confirmed'}
            <span class="ok">✓ confirmed</span>
          {:else if leg.confirmation === 'wrong'}
            <span class="bad">✗ marked wrong</span>
          {:else}
            <button
              disabled={busy}
              data-testid="confirm-train-{seg.seg_index}"
              onclick={() =>
                label({
                  type: 'train_match',
                  trip_id: detail.trip.trip_id,
                  seg_index: seg.seg_index,
                  value: 'confirmed',
                })}>✓ right train</button>
            <button
              disabled={busy}
              onclick={() =>
                label({
                  type: 'train_match',
                  trip_id: detail.trip.trip_id,
                  seg_index: seg.seg_index,
                  value: 'wrong',
                })}>✗ wrong train</button>
          {/if}
        </div>
      {/if}
    </div>
  {/each}
</section>

<section class="trip-actions">
  <button
    disabled={busy || detail.trip.reviewed}
    data-testid="mark-reviewed"
    onclick={() =>
      label({ type: 'trip_reviewed', trip_id: detail.trip.trip_id, value: true })}
  >
    {detail.trip.reviewed ? '✓ Reviewed' : 'Mark reviewed'}
  </button>
  {#if detail.trip.flag === 'phantom'}
    <button
      disabled={busy}
      onclick={() => label({ type: 'trip_flag', trip_id: detail.trip.trip_id, value: 'ok' })}
    >Unflag phantom</button>
  {:else}
    <button
      disabled={busy}
      onclick={() =>
        label({ type: 'trip_flag', trip_id: detail.trip.trip_id, value: 'phantom' })}
    >Flag as phantom</button>
  {/if}
</section>

<style>
  .segments { margin-top: 1.5rem; }
  .segment {
    display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center;
    padding: 0.6rem 0.4rem; border-bottom: 1px solid #eee;
  }
  .swatch { width: 14px; height: 14px; border-radius: 3px; display: inline-block; }
  .time { font-variant-numeric: tabular-nums; }
  .dur { color: #777; }
  .labeled { font-size: 0.8rem; color: #6a1b9a; }
  .train { flex-basis: 100%; padding-left: 1.6rem; color: #333; }
  .ok { color: #2e7d32; }
  .bad { color: #c62828; }
  .trip-actions { margin-top: 1.25rem; display: flex; gap: 0.75rem; }
  button { cursor: pointer; }
</style>
