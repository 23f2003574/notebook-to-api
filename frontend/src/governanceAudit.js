import { dashboardFetch } from './dashboardApi'
import { errorFromResponse } from './errorMessage'

export const AUDIT_OPERATIONS = ['notebook.compile', 'notebook.deploy']

export function operationLabel(action) {
  return action === 'notebook.compile' ? 'Compile' : action === 'notebook.deploy' ? 'Deploy' : action
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

// Runs the server-side chain verification. "unavailable" (could not be checked) is deliberately
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
  // Verifying records its own audit_verification event, so a chain that was empty has 1 afterwards.
  return result.checked === 0 ? { kind: 'empty' } : { kind: 'valid', checked: result.checked }
}
