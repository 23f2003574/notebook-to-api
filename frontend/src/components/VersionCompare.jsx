import { useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { signature } from '../versionDiff'

const CURRENT = 'current'

const label = (version) => `${new Date(version.saved_at).toLocaleString()} (${version.version_id.slice(0, 15)})`

// Compare two saved versions of a notebook (or one with the current notebook) and list its data files,
// using the dashboard API's own version diff and companion-file listing.
export default function VersionCompare({ filename, versions }) {
  const [from, setFrom] = useState('')
  const [against, setAgainst] = useState(CURRENT)
  const [diff, setDiff] = useState({ status: 'idle' })
  const [files, setFiles] = useState({ status: 'idle' })
  const base = `/api/notebooks/${encodeURIComponent(filename)}`
  const selected = from || versions[0]?.version_id || ''

  const compare = async () => {
    setDiff({ status: 'loading' })
    try {
      const query = against === CURRENT ? '' : `?against=${encodeURIComponent(against)}`
      const response = await dashboardFetch(`${base}/versions/${encodeURIComponent(selected)}/diff${query}`)
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not compare the versions'))
      const data = await response.json()
      setDiff({ status: 'ready', added: data.added || [], removed: data.removed || [], changed: data.changed || [] })
    } catch (err) {
      setDiff({ status: 'error', message: err.message })
    }
  }

  const listFiles = async () => {
    setFiles({ status: 'loading' })
    try {
      const response = await dashboardFetch(`${base}/files`)
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not list the data files'))
      const data = await response.json()
      setFiles({ status: 'ready', items: Array.isArray(data.files) ? data.files : [] })
    } catch (err) {
      setFiles({ status: 'error', message: err.message })
    }
  }

  const sameVersion = against === selected
  const button = 'text-xs text-blue-400 hover:text-blue-300 disabled:text-slate-600'
  const select = 'bg-slate-800 border border-slate-600 rounded px-1 py-0.5 text-xs'
  return (
    <div className="mt-3 border-t border-slate-700 pt-2" data-testid={'compare-' + filename}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label>Compare{' '}
          <select className={select} value={selected} onChange={(e) => { setFrom(e.target.value); setDiff({ status: 'idle' }) }}>
            {versions.map((v) => <option key={v.version_id} value={v.version_id}>{label(v)}</option>)}
          </select>
        </label>
        <label>with{' '}
          <select className={select} value={against} onChange={(e) => { setAgainst(e.target.value); setDiff({ status: 'idle' }) }}>
            <option value={CURRENT}>the current notebook</option>
            {versions.map((v) => <option key={v.version_id} value={v.version_id}>{label(v)}</option>)}
          </select>
        </label>
        <button type="button" className={button} disabled={!selected || sameVersion || diff.status === 'loading'} onClick={compare}>
          {diff.status === 'loading' ? 'Comparing...' : 'Compare versions'}
        </button>
        <button type="button" className={button} disabled={files.status === 'loading'} onClick={listFiles}>
          {files.status === 'loading' ? 'Loading...' : 'Show data files'}
        </button>
      </div>
      <div aria-live="polite">
        {sameVersion && <p className="text-xs text-slate-400 mt-1">Choose two different versions to compare.</p>}
        {diff.status === 'error' && <p role="alert" className="text-xs text-red-400 mt-1">❌ {diff.message}</p>}
        {diff.status === 'ready' && (
          <div className="mt-2 text-xs" data-testid={'diff-' + filename}>
            {!diff.added.length && !diff.removed.length && !diff.changed.length && (
              <p className="text-slate-400">No differences in the endpoint functions.</p>
            )}
            {diff.added.length > 0 && (
              <section aria-label="Added functions"><h4 className="text-emerald-400">Added</h4>
                <ul>{diff.added.map((fn) => <li key={fn.name}><code>{signature(fn)}</code></li>)}</ul></section>
            )}
            {diff.removed.length > 0 && (
              <section aria-label="Removed functions"><h4 className="text-red-400">Removed</h4>
                <ul>{diff.removed.map((fn) => <li key={fn.name}><code>{signature(fn)}</code></li>)}</ul></section>
            )}
            {diff.changed.length > 0 && (
              <section aria-label="Changed functions"><h4 className="text-amber-400">Changed</h4>
                <ul>{diff.changed.map((fn) => (
                  <li key={fn.name}>
                    <code>{signature(fn.old)}</code> → <code>{signature(fn.new)}</code>
                    {signature(fn.old) === signature(fn.new) && <span className="text-slate-500"> (settings or documentation changed)</span>}
                  </li>
                ))}</ul></section>
            )}
          </div>
        )}
        {files.status === 'error' && <p role="alert" className="text-xs text-red-400 mt-1">❌ {files.message}</p>}
        {files.status === 'ready' && (
          <div className="mt-2 text-xs" data-testid={'files-' + filename}>
            {files.items.length === 0 ? <p className="text-slate-400">This notebook has no data files.</p> : (
              <ul aria-label="Data files">{files.items.map((f) => (
                <li key={f.path}><code>{f.path}</code> <span className="text-slate-500">{f.bytes} bytes</span></li>
              ))}</ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
