// Headers for requests to the dashboard API. When the dashboard runs with
// NOTEBOOK_API_DASHBOARD_KEY, build the frontend with VITE_DASHBOARD_API_KEY
// set to the same value; unset, no key header is sent.
export function dashboardHeaders(extra = {}) {
  const key = import.meta.env.VITE_DASHBOARD_API_KEY
  return key ? { ...extra, 'X-API-Key': key } : extra
}

// Base URLs are fixed at build time (Vite inlines import.meta.env.VITE_*): set them when
// running `vite` / `vite build`. Anything VITE_-prefixed ends up in the public bundle, so
// never put a secret in them.
//
// A configured value is an absolute http(s) URL, optionally with a base path
// (https://host/dashboard), or a path on the page's own origin (/dashboard). Unset:
//   - development (`vite`, tests): the local defaults below;
//   - production dashboard API: the page's own origin, which is how `python -m backend.dashboard`
//     serves a built frontend;
//   - production compiled application: there is no same-origin equivalent, so it must be set.
export const DEFAULT_DASHBOARD_API_URL = 'http://localhost:8001'
export const DEFAULT_APP_URL = 'http://localhost:8000'

// Trims whitespace and trailing slashes so joining a "/path" never yields "//path".
export function normalizeBaseUrl(value, fallback) {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  return (trimmed || fallback).replace(/\/+$/, '')
}

function join(base, path) {
  if (!path) return base
  return base + (path.startsWith('/') ? path : '/' + path)
}

// `devDefault` is '' in production builds (the call sites below pick it with import.meta.env.PROD,
// which Vite replaces at build time) so the development URLs are not embedded in the bundle.
function resolveBase(name, value, devDefault, requiredInProduction) {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  if (!trimmed) {
    if (devDefault) return devDefault
    if (requiredInProduction) {
      throw new Error(`${name} is not set. Set it when building (vite build), e.g. ${name}=https://api.example.com`)
    }
    return ''
  }
  const absolute = /^https?:\/\/[^/\s]/i.test(trimmed)
  const samePath = trimmed.startsWith('/') && !trimmed.startsWith('//')
  if (!absolute && !samePath) {
    throw new Error(`${name} must be an absolute http(s) URL such as https://host or a path such as /dashboard (got "${trimmed}")`)
  }
  return normalizeBaseUrl(trimmed, '')
}

const dashboardBase = () =>
  resolveBase('VITE_DASHBOARD_API_URL', import.meta.env.VITE_DASHBOARD_API_URL,
    import.meta.env.PROD ? '' : DEFAULT_DASHBOARD_API_URL, false)
const appBase = () =>
  resolveBase('VITE_APP_URL', import.meta.env.VITE_APP_URL, import.meta.env.PROD ? '' : DEFAULT_APP_URL, true)

// The dashboard backend (upload, inspect, compile, its own /docs).
export function dashboardApiUrl(path = '') {
  return join(dashboardBase(), path)
}

// The compiled/generated application (endpoint tester requests, its /docs).
export function appUrl(path = '') {
  return join(appBase(), path)
}

// A message describing a missing or invalid setting, or null when both resolve.
export function configError() {
  try {
    dashboardBase()
    appBase()
    return null
  } catch (error) {
    return error.message
  }
}
