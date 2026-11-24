import { useEffect, useState } from 'react'
import { allows, dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'

const KINDS = {
  compile: {
    label: 'Compiles',
    path: '/api/compile/history',
    time: (e) => e.compiled_at,
    subject: (e) => e.notebook_filename || 'whole project',
    detail: (e) => [
      Number.isInteger(e.endpoint_count) && `${e.endpoint_count} endpoint${e.endpoint_count === 1 ? '' : 's'}`,
      Number.isInteger(e.dependency_count) && `${e.dependency_count} dependenc${e.dependency_count === 1 ? 'y' : 'ies'}`,
      e.version_id && `version ${e.version_id}`,
    ],
    hash: (e) => e.source_notebook_sha256,
  },
  deploy: {
    label: 'Deployments',
    path: '/api/deploy/history',
    time: (e) => e.deployed_at,
    subject: (e) => e.source_notebook_filename || e.tag || 'deployment',
    detail: (e) => [e.tag && `image ${e.tag}`, e.platform && `platform ${e.platform}`, e.pushed ? 'pushed' : 'built locally'],
    hash: (e) => e.source_notebook_sha256,
  },
}

// Compile and deploy history (GET /api/{compile,deploy}/history). Entries are recorded when an operation
// succeeds; failed attempts are in the governance audit trail, which this panel links to. Only a
// whitelist of fields is shown, so nothing an entry might carry beyond them can leak into the page.
export default function OperationHistory({ caller = null, refreshKey }) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState('compile')
  const [state, setState] = useState({ key: null, status: 'idle', entries: [], error: '' })
  const [confirming, setConfirming] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [message, setMessage] = useState(null)
  const canClear = allows(caller, 'clear_history')
  const spec = KINDS[kind]

  const [reloads, setReloads] = useState(0)
  const key = `${kind}|${refreshKey}|${reloads}`
  const loading = open && state.key !== key

  useEffect(() => {
    if (!open) return undefined
    const controller = new AbortController()
    ;(async () => {
      try {
        const response = await dashboardFetch(spec.path, { signal: controller.signal })
        if (!response.ok) throw new Error(await errorFromResponse(response, `Could not load the ${kind} history`))
        const data = await response.json()
        if (!controller.signal.aborted) setState({ key, status: 'ready', entries: Array.isArray(data.entries) ? data.entries : [], error: '' })
      } catch (err) {
        if (!controller.signal.aborted) setState({ key, status: 'error', entries: [], error: err.message })
      }
    })()
    return () => controller.abort()
  }, [open, key, spec.path, kind])

  const clear = async () => {
    setClearing(true)
    setMessage(null)
    try {
      const response = await dashboardFetch(spec.path, { method: 'DELETE' })
      if (!response.ok) throw new Error(await errorFromResponse(response, `Could not clear the ${kind} history`))
      const data = await response.json()
      setMessage({ kind: 'ok', text: `Cleared ${data.deleted_count ?? 0} ${kind} history entr${data.deleted_count === 1 ? 'y' : 'ies'}` })
      setConfirming(false)
      setReloads((n) => n + 1)
    } catch (err) {
      setMessage({ kind: 'error', text: err.message })
    } finally {
      setClearing(false)
    }
  }

  return (
    <section aria-label="Compile and deploy history">
      <button type="button" aria-expanded={open} className="text-sm text-blue-400 hover:text-blue-300" onClick={() => setOpen(!open)}>
        {open ? 'Hide history' : 'Show compile and deploy history'}
      </button>
      {open && (
        <div className="mt-3 text-sm text-slate-200">
          <div role="group" aria-label="History type" className="flex gap-3">
            {Object.entries(KINDS).map(([key, value]) => (
              <button key={key} type="button" aria-pressed={kind === key}
                className={kind === key ? 'text-white underline' : 'text-slate-400 hover:text-white'}
                onClick={() => { setKind(key); setConfirming(false); setMessage(null) }}>{value.label}</button>
            ))}
          </div>
          {loading && <p role="status" className="mt-2 text-slate-400">Loading {kind} history...</p>}
          {!loading && state.status === 'error' && <p role="alert" className="mt-2 text-red-400">❌ {state.error}</p>}
          {!loading && state.status === 'ready' && state.entries.length === 0 && (
            <p className="mt-2 text-slate-400">No {kind} history yet: an entry is recorded each time one succeeds.</p>
          )}
          {!loading && state.status === 'ready' && state.entries.length > 0 && (
            <ul className="mt-2 space-y-1" data-testid={`history-${kind}`}>
              {[...state.entries].reverse().map((entry, index) => (
                <li key={`${spec.time(entry)}-${index}`} className="bg-slate-900 rounded px-3 py-1">
                  <span className="text-emerald-400">✅ Succeeded</span>{' '}
                  <time dateTime={spec.time(entry)}>{spec.time(entry) ? new Date(spec.time(entry)).toLocaleString() : 'unknown time'}</time>{' '}
                  <code>{spec.subject(entry)}</code>
                  <span className="ml-2 text-xs text-slate-400">{spec.detail(entry).filter(Boolean).join(' · ')}</span>
                  {spec.hash(entry) && <span className="ml-2 text-xs text-slate-500">sha256 {String(spec.hash(entry)).slice(0, 12)}</span>}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-slate-400">
            Failed attempts are not listed here: <a href="#governance-audit" className="underline text-blue-300">open the audit trail</a>.
          </p>
          {message && (
            <p role={message.kind === 'error' ? 'alert' : 'status'} className={'mt-2 ' + (message.kind === 'error' ? 'text-red-400' : 'text-emerald-400')}>
              {message.kind === 'error' ? '❌ ' : '✅ '}{message.text}
            </p>
          )}
          {!loading && state.status === 'ready' && state.entries.length > 0 && (confirming ? (
            <div role="alertdialog" aria-label={`Clear ${kind} history`} className="mt-2 text-xs">
              <p>Clear the whole {kind} history? This cannot be undone.</p>
              <button type="button" className="text-red-400 hover:text-red-300 mr-3" disabled={clearing} onClick={clear}>
                {clearing ? 'Clearing...' : `Yes, clear ${kind} history`}
              </button>
              <button type="button" className="text-slate-300" onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          ) : (
            <button type="button" className="mt-2 text-xs text-red-400 hover:text-red-300 disabled:opacity-50" disabled={!canClear}
              title={canClear ? undefined : `Your key's ${caller?.scope} scope cannot clear history`}
              onClick={() => setConfirming(true)}>Clear {kind} history</button>
          ))}
        </div>
      )}
    </section>
  )
}
