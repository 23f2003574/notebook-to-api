import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Dashboard from './components/Dashboard'
import EndpointTester from './components/EndpointTester'
import NotebookUpload from './components/NotebookUpload'
import { appUrl, configError, dashboardApiUrl, normalizeBaseUrl } from './dashboardApi'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

const ok = (body) => ({ ok: true, status: 200, json: async () => body })

// Dashboard polls GET /api/health on mount; tests that script the other requests' replies in
// order keep their mock for everything except that health probe.
const stubFetch = (mock) =>
  vi.stubGlobal('fetch', (url, init) =>
    /\/api\/health$/.test(String(url))
      ? Promise.resolve({ ok: true, json: async () => ({ status: 'healthy' }) })
      : mock(url, init))

describe('base URL configuration', () => {
  it('keeps the local development defaults', () => {
    expect(dashboardApiUrl('/api/upload')).toBe('http://localhost:8001/api/upload')
    expect(appUrl('/docs')).toBe('http://localhost:8000/docs')
    expect(dashboardApiUrl()).toBe('http://localhost:8001')
  })

  it('uses custom URLs for each service independently', () => {
    vi.stubEnv('VITE_DASHBOARD_API_URL', 'https://dash.example.com')
    expect(dashboardApiUrl('/api/compile')).toBe('https://dash.example.com/api/compile')
    expect(appUrl('/docs')).toBe('http://localhost:8000/docs')
    vi.stubEnv('VITE_APP_URL', 'https://api.example.com:9000')
    expect(appUrl('/double')).toBe('https://api.example.com:9000/double')
  })

  it('normalises trailing slashes, whitespace and missing leading slashes', () => {
    expect(normalizeBaseUrl(' https://x.example.com/// ', 'd')).toBe('https://x.example.com')
    expect(normalizeBaseUrl('', 'http://d/')).toBe('http://d')
    expect(normalizeBaseUrl(undefined, 'http://d')).toBe('http://d')
    vi.stubEnv('VITE_APP_URL', 'https://api.example.com/')
    expect(appUrl('/add')).toBe('https://api.example.com/add')
    expect(appUrl('add')).toBe('https://api.example.com/add')
    expect(appUrl('')).toBe('https://api.example.com')
  })

  it('falls back to the default for a blank value', () => {
    vi.stubEnv('VITE_DASHBOARD_API_URL', '   ')
    expect(dashboardApiUrl('/x')).toBe('http://localhost:8001/x')
  })
})

describe('every audited call site uses the configuration', () => {
  const configure = () => {
    vi.stubEnv('VITE_DASHBOARD_API_URL', 'https://dash.example.com/')
    vi.stubEnv('VITE_APP_URL', 'https://api.example.com/')
  }

  it('upload, inspect and compile requests plus both docs links', async () => {
    configure()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(ok({ functions: [{ name: 'f' }], generated_files: ['app.py'], endpoints: [{ path: '/f', method: 'POST' }] }))
      .mockResolvedValueOnce(ok({ functions: [] }))
    stubFetch(fetchMock)
    const { container } = render(<Dashboard />)
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    await screen.findByText(/Found 1 functions/)
    fireEvent.click(screen.getByText(/Compile Now/))
    await screen.findByText(/Compilation successful/)

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      'https://dash.example.com/api/upload',
      'https://dash.example.com/api/inspect',
      'https://dash.example.com/api/compile',
    ])
    const hrefs = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs).toContain('https://dash.example.com/docs')
    expect(hrefs).toContain('https://api.example.com/docs')
  })

  it('upload component alone', async () => {
    configure()
    const fetchMock = vi.fn().mockResolvedValue(ok({}))
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<NotebookUpload onSuccess={() => {}} />)
    fireEvent.change(container.querySelector('input'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock.mock.calls[0][0]).toBe('https://dash.example.com/api/upload')
  })

  it('endpoint tester posts to the configured application', async () => {
    configure()
    const fetchMock = vi.fn().mockResolvedValue({ status: 200, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<EndpointTester endpoints={['/add']} functions={[]} />)
    fireEvent.change(container.querySelector('select'), { target: { value: '/add' } })
    fireEvent.click(screen.getByText('Send Request'))
    await screen.findByText('HTTP 200')
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.com/add')
  })
})

describe('no stray hardcoded URLs', () => {
  const sources = import.meta.glob(['./**/*.js', './**/*.jsx', '!./**/*.test.*', '!./setupTests.js'], {
    query: '?raw', import: 'default', eager: true,
  })

  it('keeps localhost only as the documented defaults in dashboardApi.js', () => {
    const offenders = Object.entries(sources)
      .filter(([, text]) => /localhost|127\.0\.0\.1/.test(text))
      .map(([file]) => file)
    expect(Object.keys(sources).length).toBeGreaterThan(5)
    expect(offenders).toEqual(['./dashboardApi.js'])
  })
})

describe('URL resolution across deployments', () => {
  const header = () => dashboardApiUrl('/api/health')
  const production = (env = {}) => {
    vi.stubEnv('PROD', true)
    vi.stubEnv('VITE_APP_URL', 'https://api.example.com')
    Object.entries(env).forEach(([k, v]) => vi.stubEnv(k, v))
  }

  it('development defaults still point at the local services', () => {
    vi.stubEnv('VITE_DASHBOARD_API_URL', '')
    vi.stubEnv('VITE_APP_URL', '')
    expect(header()).toBe('http://localhost:8001/api/health')
    expect(appUrl('/add')).toBe('http://localhost:8000/add')
  })

  it('a production build with no dashboard URL uses the page\'s own origin, never localhost', () => {
    production({ VITE_DASHBOARD_API_URL: '' })
    expect(header()).toBe('/api/health')
    expect(dashboardApiUrl()).toBe('')
  })

  it('an explicit origin is respected, with or without trailing slashes', () => {
    production({ VITE_DASHBOARD_API_URL: ' https://dash.example.com// ' })
    expect(header()).toBe('https://dash.example.com/api/health')
    expect(dashboardApiUrl('api/health')).toBe('https://dash.example.com/api/health')
  })

  it('keeps a configured base path, absolute or same-origin', () => {
    production({ VITE_DASHBOARD_API_URL: 'https://example.com/tools/dashboard/' })
    expect(header()).toBe('https://example.com/tools/dashboard/api/health')
    vi.stubEnv('VITE_DASHBOARD_API_URL', '/dashboard/')
    expect(header()).toBe('/dashboard/api/health')
    vi.stubEnv('VITE_DASHBOARD_API_URL', '/')
    expect(header()).toBe('/api/health')
  })

  it('rejects settings that would silently resolve to the wrong place', () => {
    production()
    for (const bad of ['localhost:8001', 'dashboard.example.com', '//evil.example.com', 'ftp://x.example.com']) {
      vi.stubEnv('VITE_DASHBOARD_API_URL', bad)
      expect(() => header()).toThrow(/VITE_DASHBOARD_API_URL must be an absolute http\(s\) URL/)
    }
  })

  it('requires the compiled application URL in production and says how to set it', () => {
    production({ VITE_APP_URL: '' })
    expect(() => appUrl('/add')).toThrow(/VITE_APP_URL is not set\. Set it when building/)
    expect(configError()).toMatch(/VITE_APP_URL/)
  })

  it('reports no problem for a valid configuration', () => {
    production({ VITE_DASHBOARD_API_URL: 'https://dash.example.com' })
    expect(configError()).toBeNull()
  })

  it('sends the dashboard key with requests to a configured URL', async () => {
    production({ VITE_DASHBOARD_API_URL: 'https://dash.example.com/base', VITE_DASHBOARD_API_KEY: 'k-123' })
    const fetchMock = vi.fn().mockResolvedValue(ok({ filename: 'n.ipynb' }))
    vi.stubGlobal('fetch', fetchMock)
    const view = render(<NotebookUpload onSuccess={vi.fn()} />)
    fireEvent.change(view.container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock.mock.calls[0][0]).toBe('https://dash.example.com/base/api/upload')
    expect(fetchMock.mock.calls[0][1].headers['X-API-Key']).toBe('k-123')
  })

  it('shows a visible configuration problem instead of failing silently', async () => {
    production({ VITE_APP_URL: '' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ status: 'healthy' })))
    render(<Dashboard />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/VITE_APP_URL is not set/)
  })
})
