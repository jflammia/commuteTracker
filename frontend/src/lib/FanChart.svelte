<!-- Each option as a horizontal P50–P90 band positioned on a time axis. -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { mountChart } from './echarts';
  import type { ItineraryOption } from './api';

  let { options }: { options: ItineraryOption[] } = $props();
  let el: HTMLDivElement;

  function ms(iso: string): number {
    return new Date(iso).getTime();
  }

  onMount(() => {
    const rows = options.map((o) => o.gtfs_trip_id);
    const p50 = options.map((o) => [ms(o.p50_arrive), o.gtfs_trip_id]);
    const p90 = options.map((o) => [ms(o.p90_arrive), o.gtfs_trip_id]);
    return mountChart(el, {
      grid: { left: 90, right: 24, top: 16, bottom: 40 },
      xAxis: { type: 'time', name: 'arrival' },
      yAxis: { type: 'category', data: rows },
      series: [
        { type: 'line', data: p50, symbol: 'circle', symbolSize: 8,
          lineStyle: { opacity: 0 }, name: 'P50' },
        { type: 'line', data: p90, symbol: 'diamond', symbolSize: 8,
          lineStyle: { opacity: 0 }, name: 'P90' },
      ],
      tooltip: { trigger: 'item' },
    });
  });
</script>

<div bind:this={el} class="chart" data-testid="fan-chart"></div>

<style>.chart { height: 260px; width: 100%; }</style>
