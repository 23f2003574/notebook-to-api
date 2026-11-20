const asList = (value) => (Array.isArray(value) ? value : [])

// Compiler diagnostics the backend returns from inspect and compile: functions that got no endpoint
// (and why), endpoints that run in the background only because of their name, and startup warnings.
export const emptyDiagnostics = { skipped: [], inferredBackground: [], warnings: [] }

export const diagnosticsFrom = (data) => ({
  skipped: asList(data?.skipped_functions).filter((f) => f && typeof f.name === 'string'),
  inferredBackground: asList(data?.inferred_background).filter((n) => typeof n === 'string'),
  warnings: asList(data?.startup_warnings).filter((w) => typeof w === 'string'),
})
