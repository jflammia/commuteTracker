import path from 'path';
import { fileURLToPath } from 'url';
import { defineConfig } from '@playwright/test';

// ESM-safe __dirname equivalent
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// __dirname is frontend/ — build is always adjacent
const buildDir = path.resolve(__dirname, 'build');
// repo root is one level up from frontend/
const repoRoot = path.resolve(__dirname, '..');

export default defineConfig({
  testDir: 'e2e',
  use: {
    baseURL: 'http://localhost:8093',
    // SPA with large maplibre bundle — generous timeouts
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  expect: { timeout: 15_000 },
  webServer: {
    cwd: repoRoot,
    command: `sh -c "rm -rf /tmp/ct-e2e-data && python -m backend.tests.e2e_seed /tmp/ct-e2e-data && CT_DATA_DIR=/tmp/ct-e2e-data CT_FRONTEND_BUILD_DIR=${buildDir} uvicorn backend.app:app --port 8093"`,
    url: 'http://localhost:8093/api/health/ingestion',
    timeout: 60_000,
    reuseExistingServer: false,
  },
});
