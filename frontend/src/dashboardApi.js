// Headers for requests to the dashboard API. When the dashboard runs with
// NOTEBOOK_API_DASHBOARD_KEY, build the frontend with VITE_DASHBOARD_API_KEY
// set to the same value; unset, no key header is sent.
export function dashboardHeaders(extra = {}) {
  const key = import.meta.env.VITE_DASHBOARD_API_KEY
  return key ? { ...extra, 'X-API-Key': key } : extra
}

// Base URLs are fixed at build time (Vite inlines import.meta.env.VITE_*): set them when
// running `vite` / `vite build`. Unset or blank, the local development defaults apply.
// Anything VITE_-prefixed ends up in the public bundle, so never put a secret in them.
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

// The dashboard backend (upload, inspect, compile, its own /docs).
export function dashboardApiUrl(path = '') {
  return join(normalizeBaseUrl(import.meta.env.VITE_DASHBOARD_API_URL, DEFAULT_DASHBOARD_API_URL), path)
}

// The compiled/generated application (endpoint tester requests, its /docs).
export function appUrl(path = '') {
  return join(normalizeBaseUrl(import.meta.env.VITE_APP_URL, DEFAULT_APP_URL), path)
}
