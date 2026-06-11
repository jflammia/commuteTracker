import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: {
      '/api': 'http://localhost:8090',
      '/ingest': 'http://localhost:8090',
    },
  },
  // Vitest and Playwright both claim *.spec.ts; scope vitest to src/ so it
  // doesn't try to run the Playwright e2e specs (which live in e2e/).
  test: {
    include: ['src/**/*.{test,spec}.{js,ts}'],
  },
});
