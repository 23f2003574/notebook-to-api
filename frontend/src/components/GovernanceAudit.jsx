import { useCallback, useEffect, useState } from 'react'
import { fetchOperationRecords, operationLabel } from '../governanceAudit'

function Record({ record }) {
  const ok = record.outcome === 'success'
  const meta = record.metadata || {}
  const { notebook, error, ...provenance } = meta
  return (
    <li className="rounded border border-slate-700 p-3" data-testid={`audit-record-${record.sequence}`}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-slate-400">#{record.sequence}</span>
        <span className="font-semibold text-white">{operationLabel(record.action)}</span>
        <code className="text-blue-300">{notebook || 'generated app'}</code>
        <span className={ok ? 'text-green-400' : 'text-red-400'} data-testid="audit-outcome">
          {ok ? '✓ success' : '✗ failure'}
        </span>
        <time className="ml-auto text-slate-400" dateTime={record.occurred_at}>
          {new Date(record.occurred_at).toLocaleString()}
        </time>
      </div>
      {error && <p className="mt-2 text-sm text-red-300">{error}</p>}
      {Object.keys(provenance).length > 0 && (
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 text-sm text-slate-300">
          {Object.entries(provenance).map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-slate-400">{key}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}

export default function GovernanceAudit({ refreshKey }) {
  const [state, setState] = useState({ status: 'loading', records: [], error: '' })

  const load = useCallback(async () => {
    setState((s) => ({ ...s, status: 'loading', error: '' }))
    try {
      setState({ status: 'ready', records: await fetchOperationRecords(), error: '' })
    } catch (err) {
      setState({ status: 'error', records: [], error: err.message || 'Could not load the audit records' })
    }
  }, [])

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load() }, [load, refreshKey])

  return (
    <div data-testid="governance-audit">
      {state.status === 'loading' && <p className="text-slate-400">Loading audit records…</p>}
      {state.status === 'error' && (
        <div role="alert" className="text-red-300">
          <p>{state.error}</p>
          <button type="button" className="mt-2 underline" onClick={load}>Retry</button>
        </div>
      )}
      {state.status === 'ready' && state.records.length === 0 && (
        <p className="text-slate-400">No compile or deploy operations have been recorded yet.</p>
      )}
      {state.status === 'ready' && state.records.length > 0 && (
        <ul className="space-y-2">{state.records.map((r) => <Record key={r.sequence} record={r} />)}</ul>
      )}
    </div>
  )
}
