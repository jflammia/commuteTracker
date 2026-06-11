<script lang="ts">
  let { children } = $props();
</script>

<nav>
  <span class="brand">CommuteTracker</span>
  <span class="placeholder" title="Phase 5">Today</span>
  <a href="/trips">Trips</a>
  <span class="placeholder" title="Phase 5">Optimizer</span>
  <span class="placeholder" title="Phase 6">Trends</span>
  <span class="placeholder" title="Phase 6">Health</span>
</nav>
<main>
  {@render children()}
</main>

<style>
  :global(body) {
    margin: 0;
    font-family: system-ui, sans-serif;
    background: #fafafa;
    color: #222;
  }
  nav {
    display: flex;
    gap: 1.25rem;
    align-items: center;
    padding: 0.75rem 1.25rem;
    background: #1a1a2e;
    color: #eee;
  }
  .brand {
    font-weight: 700;
    margin-right: 1rem;
  }
  nav a {
    color: #fff;
    text-decoration: none;
    font-weight: 600;
  }
  .placeholder {
    color: #777;
    cursor: default;
  }
  main {
    max-width: 1100px;
    margin: 1.5rem auto;
    padding: 0 1rem;
  }
</style>
