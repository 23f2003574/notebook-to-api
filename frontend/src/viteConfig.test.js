import { describe, expect, it } from 'vitest'

import { rejectBuildTimeDashboardKey } from '../vite.config.js'

describe('build-time dashboard key guard', () => {
  it('rejects a VITE_DASHBOARD_API_KEY, which would ship in the public bundle', () => {
    expect(() => rejectBuildTimeDashboardKey({ VITE_DASHBOARD_API_KEY: 'k' })).toThrow(/public JavaScript bundle/)
  })

  it('allows builds without it', () => {
    expect(() => rejectBuildTimeDashboardKey({ VITE_APP_URL: 'https://x' })).not.toThrow()
  })
})
