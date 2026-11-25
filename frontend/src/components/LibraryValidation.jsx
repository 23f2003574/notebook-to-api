import { useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { IssueList } from './NotebookValidation'

// Validates every uploaded notebook at once (GET /api/validate-all) and lists the ones that need attention.
export default function LibraryValidation({ refreshKey }) {
  const [state, setState] = useState({ key: null, status: 'idle', data: null, error: '' })

  const run = async () => {
    setState({ key: refreshKey, status: 'loading', data: null, error: '' })
    try {
      const response = await dashboardFetch('/api/validate-all')
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not validate the library'))
      setState({ key: refreshKey, status: 'ready', data: await response.json(), error: '' })
    } catch (err) {
      setState({ key: refreshKey, status: 'error', data: null, error: err.message })
    }
  }

  const data = state.data
  const flagged = (data?.results || []).filter((r) => r.status !== 'pass')
  const stale = state.status === 'ready' && state.key !== refreshKey

  return (
    <section aria-label="Validate all notebooks" className="text-slate-200">
      <button type="button" className="text-sm text-blue-400 hover:text-blue-300 disabled:opacity-50"
        disabled={state.status === 'loading'} onClick={run}>
        {state.status === 'loading' ? 'Validating...' : 'Validate all notebooks'}
      </button>
      {stale && <span className="ml-2 text-xs text-slate-400">The library changed since this check; run it again.</span>}
      {state.status === 'error' && <p role="alert" className="text-sm text-red-400 mt-1">❌ {state.error}</p>}
      {state.status === 'ready' && (
        <div className="mt-2">
          <p role="status" className="text-sm">
            {data.pass_count} passing · {data.warn_count} with warnings · {data.fail_count} failing
          </p>
          {data.result_count === 0 && <p className="text-xs text-slate-400">No notebooks uploaded yet.</p>}
          <ul className="mt-1 space-y-2">
            {flagged.map((entry) => (
              <li key={entry.filename} className="bg-slate-900 rounded px-3 py-2">
                <code>{entry.filename}</code> <span className="text-xs text-slate-400">({entry.status})</span>
                <IssueList result={entry} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
