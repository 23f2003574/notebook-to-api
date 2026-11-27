import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig } from '@playwright/test'

// Real browser, real dashboard: the built frontend is served by `python -m backend.dashboard`
// (same origin), with throwaway upload/generated/audit directories so runs are deterministic and
// need no credentials or external services. Build first: `npm run e2e` does.
const PORT = Number(process.env.E2E_PORT || 8123)
// The config is evaluated again in each worker; the env var makes them share the runner's directory.
const work = process.env.E2E_WORK_DIR || mkdtempSync(join(tmpdir(), 'n2a-e2e-'))
process.env.E2E_WORK_DIR = work

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 90_000,
  // A failure is retried once in CI only: a pass on retry is reported as "flaky" (visible in the
  // list output and the uploaded report) instead of failing the build, and the retry records a
  // trace. Locally nothing is retried, so flakiness shows up while developing.
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]] : [['list']],
  outputDir: 'test-results',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, args: ['--no-sandbox'] }
      : {},
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: process.env.E2E_PYTHON ? `${process.env.E2E_PYTHON} -m backend.dashboard` : 'python -m backend.dashboard',
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      NOTEBOOK_API_DASHBOARD_HOST: '127.0.0.1',
      NOTEBOOK_API_DASHBOARD_PORT: String(PORT),
      NOTEBOOK_API_UPLOAD_DIR: join(work, 'uploads'),
      NOTEBOOK_API_GENERATED_DIR: join(work, 'generated'),
      NOTEBOOK_API_GENERATED_APPS_DIR: join(work, 'apps'),
      NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND: 'sqlite',
      NOTEBOOK2API_GOVERNANCE_DATABASE_PATH: join(work, 'audit.db'),
      PYTHONDONTWRITEBYTECODE: '1',
    },
  },
})
