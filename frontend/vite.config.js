/* global process */
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Any VITE_* value is inlined into the public bundle, so the dashboard key must never be one:
// it is entered in the browser instead (see src/dashboardApi.js).
export function rejectBuildTimeDashboardKey(env) {
  if (env.VITE_DASHBOARD_API_KEY) {
    throw new Error(
      'VITE_DASHBOARD_API_KEY would be embedded in the public JavaScript bundle. Unset it; ' +
      'the dashboard asks for the key in the browser.'
    )
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  rejectBuildTimeDashboardKey({ ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env })
  return {
    plugins: [react()],
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './src/setupTests.js',
      include: ['src/**/*.{test,spec}.{js,jsx}'], // e2e/ belongs to Playwright
    },
  }
})
