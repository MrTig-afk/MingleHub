import { defineConfig } from '@playwright/test'

// E2E tests for the frontend-only behaviors that pytest / the API simulator can't
// reach: host-gated rounds, the drop-to-1 Waiting + countdown, and the New game
// button. Assumes the dev stack is already up:
//   - Vite on https://192.168.1.108:5174
//   - FastAPI on https://192.168.1.108:8000 (DEV_MODE=true)
// Tests share table 1 (each resets it first), so they run serially.
export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'https://192.168.1.108:5174',
    ignoreHTTPSErrors: true,
    headless: true,
    actionTimeout: 15_000,
  },
})
