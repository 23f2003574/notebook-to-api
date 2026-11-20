import { dashboardFetch } from './dashboardApi'
import { errorFromResponse } from './errorMessage'

export const AUDIT_OPERATIONS = ['notebook.compile', 'notebook.deploy']

export function operationLabel(action) {
  return action === 'notebook.compile' ? 'Compile' : action === 'notebook.deploy' ? 'Deploy' : action
}

// The governance API filters by one action at a time, so ask for each operation and merge.
// Records stay on the server: this only holds the page currently shown.
export async function fetchOperationRecords(limit = 50) {
  const lists = await Promise.all(
    AUDIT_OPERATIONS.map(async (action) => {
      const response = await dashboardFetch(`/governance/audit?action=${encodeURIComponent(action)}&limit=${limit}`)
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not load the audit records'))
      return response.json()
    }),
  )
  return lists.flat().sort((a, b) => b.sequence - a.sequence).slice(0, limit)
}
