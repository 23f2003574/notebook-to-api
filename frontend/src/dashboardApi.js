// Headers for requests to the dashboard API. When the dashboard runs with
// NOTEBOOK_API_DASHBOARD_KEY, build the frontend with VITE_DASHBOARD_API_KEY
// set to the same value; unset, no key header is sent.
export function dashboardHeaders(extra = {}) {
  const key = import.meta.env.VITE_DASHBOARD_API_KEY
  return key ? { ...extra, 'X-API-Key': key } : extra
}
