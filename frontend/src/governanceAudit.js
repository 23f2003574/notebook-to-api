import { dashboardFetch } from './dashboardApi'
import { errorFromResponse } from './errorMessage'

export const AUDIT_OPERATIONS = ['notebook.compile', 'notebook.deploy', 'notebook.delete', 'notebook.rename', 'notebook.restore', 'notebook.overwrite', 'notebook.version_delete']

const OPERATION_LABELS = {
  'notebook.compile': 'Compile',
  'notebook.deploy': 'Deploy',
  'notebook.delete': 'Delete',
  'notebook.rename': 'Rename',
  'notebook.restore': 'Restore version',
  'notebook.overwrite': 'Overwrite',
  'notebook.version_delete': 'Delete version',
}

export function operationLabel(action) {
  return OPERATION_LABELS[action] || action
}

// One page of compile/deploy history, newest first, filtered on the server. Pass the previous
// page's nextCursor to continue; nothing beyond the requested page is ever loaded.
export async function fetchHistory({ notebook = '', action = '', outcome = '', cursor = '', limit = 25 } = {}) {
  const params = new URLSearchParams({ scope: 'notebook', limit: String(limit) })
  if (notebook.trim()) params.set('notebook', notebook.trim())
  if (action) params.set('action', action)
  if (outcome) params.set('outcome', outcome)
  if (cursor) params.set('cursor', cursor)
  const response = await dashboardFetch(`/governance/audit/history?${params}`)
  if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not load the audit records'))
  const body = await response.json()
  return { records: Array.isArray(body.records) ? body.records : [], hasMore: Boolean(body.has_more), nextCursor: body.next_cursor || '' }
}

// One record by its stable sequence number: what a compile or deploy result links to.
export async function fetchRecord(sequence) {
  const response = await dashboardFetch(`/governance/audit/${encodeURIComponent(sequence)}`)
  if (response.status === 404) return null
  if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not load the audit record'))
  return response.json()
}

// Runs the server-side chain verification (GET: strictly read-only, it never adds a record). "unavailable" (could not be checked) is deliberately
// different from "broken" (checked, and tampering or loss was found) and from "empty".
export async function verifyChain() {
  let response
  try {
    response = await dashboardFetch('/governance/audit/verify')
  } catch (err) {
    return { kind: 'unavailable', message: err.message || 'The dashboard could not be reached' }
  }
  if (!response.ok) {
    return { kind: 'unavailable', message: await errorFromResponse(response, 'The audit chain could not be verified') }
  }
  const result = await response.json()
  if (!result.valid) return { kind: 'broken', checked: result.checked, sequence: result.first_broken_sequence, reason: result.reason }
  return result.checked === 0 ? { kind: 'empty' } : { kind: 'valid', checked: result.checked, integrity: result.integrity || 'unkeyed' }
}

// The server's own description of what is guaranteed and how to recover (GET /governance/audit/recovery).
export async function fetchRecoveryGuide() {
  const response = await dashboardFetch('/governance/audit/recovery')
  if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not load the recovery guide'))
  return response.json()
}
