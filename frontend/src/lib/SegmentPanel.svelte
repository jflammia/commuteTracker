<script lang="ts">
  import type { TripDetail } from './api';
  let { detail, onchange }: { detail: TripDetail; onchange: () => void } = $props();
</script>

<p>{detail.segments.length} segments</p>
