import { useCallback, useEffect, useState } from 'react'
import { AUDIT_OPERATIONS, fetchHistory, fetchRecord, fetchRecoveryGuide, operationLabel, verifyChain } from '../governanceAudit'

function Record({ record, linked }) {
  const ok = record.outcome === 'success'
  const meta = record.metadata || {}
  const { notebook, error, ...provenance } = meta
  return (
    <li className={`rounded border p-3 ${linked ? 'border-blue-400' : 'border-slate-700'}`}
      data-testid={`audit-record-${record.sequence}`} aria-current={linked ? 'true' : undefined}>
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

function RecoveryGuide() {
  const [guide, setGuide] = useState(null)
  const [error, setError] = useState('')
  const onToggle = async (event) => {
    if (!event.currentTarget.open || guide) return
    try {
      setGuide(await fetchRecoveryGuide())
    } catch (err) {
      setError(err.message)
    }
  }
  const sections = [['guarantees', 'What is guaranteed'], ['if_unavailable', 'If the storage is unavailable'], ['if_broken', 'If verification fails']]
  return (
    <details onToggle={onToggle} className="text-sm text-slate-300" data-testid="recovery-guide">
      <summary className="cursor-pointer">How recovery works</summary>
      {error && <p className="text-red-300">{error}</p>}
      {guide && sections.map(([key, title]) => (
        <div key={key} className="mt-2">
          <p className="font-semibold text-slate-200">{title}</p>
          <ul className="list-disc pl-5">{(guide[key] || []).map((line) => <li key={line}>{line}</li>)}</ul>
        </div>
      ))}
    </details>
  )
}

function VerificationResult({ verification }) {
  if (!verification || verification.kind === 'running') return null
  const common = { 'data-testid': 'verification', role: 'status' }
  if (verification.kind === 'valid') {
    return <p {...common} className="text-green-400">✓ Chain intact: {verification.checked} records checked, none altered or missing.</p>
  }
  if (verification.kind === 'empty') {
    return <p {...common} className="text-slate-300">The chain is valid but empty: nothing has been recorded yet.</p>
  }
  if (verification.kind === 'broken') {
    return (
      <p {...common} className="text-red-400">
        ✗ Chain broken at record #{verification.sequence}: {verification.reason}. Records from #{verification.sequence} on
        cannot be trusted; compare against a backup of the audit database.
      </p>
    )
  }
  return (
    <p {...common} className="text-amber-300">
      ⚠ Could not verify: {verification.message}. This does not mean the chain is broken; the check did not run to completion.
    </p>
  )
}

export default function GovernanceAudit({ refreshKey, focusSequence = null }) {
  const [draft, setDraft] = useState({ notebook: '', action: '', outcome: '' })
  const [filters, setFilters] = useState(draft)
  const [state, setState] = useState({
    status: 'loading', records: [], error: '', linked: null, linkedMissing: false, hasMore: false, nextCursor: '', loadingMore: false,
  })

  const load = useCallback(async () => {
    setState((s) => ({ ...s, status: 'loading', error: '' }))
    try {
      const [page, linked] = await Promise.all([
        fetchHistory(filters),
        focusSequence ? fetchRecord(focusSequence) : Promise.resolve(null),
      ])
      setState({
        status: 'ready', records: page.records, error: '', linked, linkedMissing: Boolean(focusSequence) && !linked,
        hasMore: page.hasMore, nextCursor: page.nextCursor, loadingMore: false,
      })
    } catch (err) {
      setState({
        status: 'error', records: [], error: err.message || 'Could not load the audit records', linked: null,
        linkedMissing: false, hasMore: false, nextCursor: '', loadingMore: false,
      })
    }
  }, [filters, focusSequence])

  const loadMore = async () => {
    setState((s) => ({ ...s, loadingMore: true }))
    try {
      const page = await fetchHistory({ ...filters, cursor: state.nextCursor })
      setState((s) => ({ ...s, records: [...s.records, ...page.records], hasMore: page.hasMore, nextCursor: page.nextCursor, loadingMore: false }))
    } catch (err) {
      setState((s) => ({ ...s, loadingMore: false, error: err.message || 'Could not load more records' }))
    }
  }

  useEffect(() => { load() }, [load, refreshKey])

  const [verification, setVerification] = useState(null)
  const verify = async () => {
    setVerification({ kind: 'running' })
    setVerification(await verifyChain())
    load() // verification records its own audit event
  }

  return (
    <div data-testid="governance-audit">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <button type="button" onClick={verify} disabled={verification?.kind === 'running'}
          className="rounded bg-slate-700 px-3 py-1 text-white disabled:opacity-50">
          {verification?.kind === 'running' ? 'Verifying…' : 'Verify audit chain'}
        </button>
        <VerificationResult verification={verification} />
      </div>
      <form className="mb-3 flex flex-wrap items-end gap-3 text-sm" onSubmit={(e) => { e.preventDefault(); setFilters(draft) }}>
        <label className="text-slate-300">Notebook
          <input value={draft.notebook} onChange={(e) => setDraft({ ...draft, notebook: e.target.value })}
            placeholder="all notebooks" className="ml-2 rounded bg-slate-900 px-2 py-1 text-white" />
        </label>
        <label className="text-slate-300">Operation
          <select value={draft.action} onChange={(e) => setDraft({ ...draft, action: e.target.value })}
            className="ml-2 rounded bg-slate-900 px-2 py-1 text-white">
            <option value="">All</option>
            {AUDIT_OPERATIONS.map((a) => <option key={a} value={a}>{operationLabel(a)}</option>)}
          </select>
        </label>
        <label className="text-slate-300">Outcome
          <select value={draft.outcome} onChange={(e) => setDraft({ ...draft, outcome: e.target.value })}
            className="ml-2 rounded bg-slate-900 px-2 py-1 text-white">
            <option value="">All</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
          </select>
        </label>
        <button type="submit" className="rounded bg-blue-600 px-3 py-1 text-white">Apply filters</button>
      </form>
      <div className="mb-3"><RecoveryGuide /></div>
      {state.status === 'loading' && <p className="text-slate-400">Loading audit records…</p>}
      {state.status === 'error' && (
        <div role="alert" className="text-red-300">
          <p>{state.error}</p>
          <button type="button" className="mt-2 underline" onClick={load}>Retry</button>
        </div>
      )}
      {state.status === 'ready' && state.linked && (
        <ul className="mb-3" data-testid="linked-record"><Record record={state.linked} linked /></ul>
      )}
      {state.status === 'ready' && state.linkedMissing && (
        <p className="mb-3 text-amber-300">Audit record #{focusSequence} was not found (it may have been purged).</p>
      )}
      {state.status === 'ready' && state.records.length === 0 && (
        <p className="text-slate-400">No compile or deploy records match.</p>
      )}
      {state.status === 'ready' && state.records.length > 0 && (
        <ul className="space-y-2">{state.records.map((r) => <Record key={r.sequence} record={r} linked={r.sequence === focusSequence} />)}</ul>
      )}
      {state.status === 'ready' && state.hasMore && (
        <button type="button" onClick={loadMore} disabled={state.loadingMore} className="mt-3 underline text-blue-300">
          {state.loadingMore ? 'Loading…' : 'Load older records'}
        </button>
      )}
    </div>
  )
}
