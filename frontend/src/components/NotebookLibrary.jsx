import { useCallback, useEffect, useRef, useState } from 'react'
import { allows, dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import VersionCompare from './VersionCompare'
import { diagnosticsFrom } from '../diagnostics'
import Diagnostics from './Diagnostics'

const formatSize = (bytes) => (bytes >= 1024 ? (bytes / 1024).toFixed(1) + ' KB' : bytes + ' B')

// Hands a Blob to the browser as a download. The files come from key-protected endpoints, so a
// plain <a href> cannot be used: the request has to carry the X-API-Key header.
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

const jsonInit = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

const stem = (filename) => filename.replace(/\.ipynb$/, '')

// Notebooks stored on the dashboard (GET /api/notebooks). Each one can be opened (inspected and
// compiled in the main panel) and has its own compiled app (compile with per_notebook), which can
// be downloaded, deployed and exported without touching any other notebook's app.
export default function NotebookLibrary({ onOpen, refreshKey, onAudit, caller = null }) {
  const can = (action) => allows(caller, action)
  const refusal = (action) => (can(action) ? undefined : `Your key's ${caller.scope} scope cannot ${action}`)
  const [notebooks, setNotebooks] = useState(null)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  const [trash, setTrash] = useState(null) // soft-deleted notebooks (loaded when the Trash is opened)
  const [trashCount, setTrashCount] = useState(0)
  const [busy, setBusy] = useState({}) // filename -> action name
  const [notes, setNotes] = useState({}) // filename -> { kind: 'ok' | 'error', text }
  const [diagnostics, setDiagnostics] = useState({}) // filename -> what its last compile reported
  const [event, setEvent] = useState(null) // last rename/delete: { text, audit } (its row may be gone)
  const [panel, setPanel] = useState({}) // filename -> 'rename' | 'delete' | 'versions'
  const [versions, setVersions] = useState({}) // filename -> { status, items, error }
  const [renameTo, setRenameTo] = useState({}) // filename -> text in the rename box
  const [pendingRestore, setPendingRestore] = useState({}) // filename -> version_id awaiting confirmation
  const triggers = useRef({}) // 'filename|panel' -> the button that opened it, to return focus to
  const focusNext = useRef(null) // element id to focus after the next render

  useEffect(() => {
    const controller = new AbortController()
    ;(async () => {
      try {
        const response = await dashboardFetch('/api/notebooks', { signal: controller.signal })
        if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not list notebooks'))
        const data = await response.json()
        const list = Array.isArray(data.notebooks) ? data.notebooks.filter((n) => n && typeof n.filename === 'string') : []
        if (!controller.signal.aborted) {
          setNotebooks(list)
          setError(null)
        }
        if (!controller.signal.aborted) setTrashCount(Number.isInteger(data.trash_count) ? data.trash_count : 0)
      } catch (err) {
        if (!controller.signal.aborted) setError(err.message)
      }
    })()
    return () => controller.abort()
  }, [refreshKey, reload])

  useEffect(() => {
    if (focusNext.current) {
      const target = document.getElementById(focusNext.current) || triggers.current[focusNext.current]
      focusNext.current = null
      if (target) target.focus()
    }
  })

  const openPanel = (filename, name) => {
    setPanel((current) => ({ ...current, [filename]: name }))
    setNotes((current) => ({ ...current, [filename]: null }))
    focusNext.current = `${name}-first-${filename}`
    if (name === 'rename') setRenameTo((current) => ({ ...current, [filename]: filename }))
    if (name === 'versions') loadVersions(filename)
  }

  const closePanel = (filename, name) => {
    setPanel((current) => ({ ...current, [filename]: null }))
    setPendingRestore((current) => ({ ...current, [filename]: null }))
    focusNext.current = `${filename}|${name}`
  }

  const loadVersions = async (filename) => {
    setVersions((current) => ({ ...current, [filename]: { status: 'loading', items: [], error: '' } }))
    try {
      const response = await dashboardFetch(`/api/notebooks/${encodeURIComponent(filename)}/versions?companions=true`)
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not load the version history'))
      const data = await response.json()
      setVersions((current) => ({ ...current, [filename]: { status: 'ready', items: Array.isArray(data.versions) ? data.versions : [], error: '' } }))
    } catch (err) {
      setVersions((current) => ({ ...current, [filename]: { status: 'error', items: [], error: err.message } }))
    }
  }

  // One action on one notebook; every other row keeps its own state.
  const run = useCallback(async (filename, action, work) => {
    setBusy((current) => ({ ...current, [filename]: action }))
    setNotes((current) => ({ ...current, [filename]: null }))
    setEvent(null)
    try {
      const outcome = await work()
      const text = typeof outcome === 'string' ? outcome : outcome.text
      if (typeof outcome === 'object' && outcome.global) setEvent({ text, audit: outcome.audit })
      else setNotes((current) => ({ ...current, [filename]: { kind: 'ok', text, audit: typeof outcome === 'string' ? null : outcome.audit } }))
    } catch (err) {
      setNotes((current) => ({ ...current, [filename]: { kind: 'error', text: err.message, audit: err.audit || null } }))
    } finally {
      setBusy((current) => {
        const next = { ...current }
        delete next[filename]
        return next
      })
    }
  }, [])

  const check = async (response, fallback) => {
    if (!response.ok) {
      const error = new Error(await errorFromResponse(response, fallback))
      error.status = response.status
      error.audit = Number(response.headers?.get?.('X-Governance-Audit-Sequence')) || null
      throw error
    }
    return response
  }

  // 404: someone else removed it (the list is refreshed); 409: the target name is taken.
  const explain = (err, filename) => {
    if (err.status === 404) {
      setReload((n) => n + 1)
      err.message = `${filename} no longer exists on the dashboard (the list was refreshed). ${err.message}`
      setEvent({ text: err.message, error: true, audit: err.audit })
    }
    throw err
  }

  // The record's reference travels in a header, so the response bodies keep their shape.
  const sequenceOf = (response) => Number(response.headers?.get?.('X-Governance-Audit-Sequence')) || null

  const renameNotebook = (filename) => run(filename, 'rename', async () => {
    const target = (renameTo[filename] || '').trim()
    if (!target) throw new Error('Enter a new name')
    try {
      const response = await check(
        await dashboardFetch('/api/notebooks/' + encodeURIComponent(filename), { ...jsonInit({ new_filename: target }), method: 'PATCH' }),
        'Rename failed',
      )
      const data = await response.json()
      closePanel(filename, 'rename')
      setReload((n) => n + 1)
      return { text: `Renamed ${filename} to ${data.new_filename || target}`, audit: sequenceOf(response), global: true }
    } catch (err) {
      return explain(err, filename)
    }
  })

  const deleteNotebook = (filename) => run(filename, 'delete', async () => {
    try {
      const response = await check(await dashboardFetch('/api/notebooks/' + encodeURIComponent(filename) + '?soft=true', { method: 'DELETE' }), 'Delete failed')
      closePanel(filename, 'delete')
      setReload((n) => n + 1)
      return { text: `Moved ${filename} to the trash`, audit: sequenceOf(response), global: true }
    } catch (err) {
      return explain(err, filename)
    }
  })

  const toggleTrash = async () => {
    if (trash !== null) {
      setTrash(null)
      return
    }
    try {
      const body = await (await check(await dashboardFetch('/api/notebooks/trash'), 'Could not load the trash')).json()
      setTrash(Array.isArray(body.items) ? body.items : [])
    } catch (err) {
      setEvent({ text: err.message })
    }
  }

  const restoreFromTrash = (item) => run(item.filename, 'untrash', async () => {
    try {
      const response = await check(await dashboardFetch('/api/notebooks/trash/' + encodeURIComponent(item.trash_id) + '/restore', { method: 'POST' }), 'Restore failed')
      setReload((n) => n + 1)
      setTrash(null)
      return { text: `Restored ${item.filename} from the trash`, audit: sequenceOf(response), global: true }
    } catch (err) {
      return { text: err.status === 409 ? `Cannot restore ${item.filename}: ${err.message}` : err.message, global: true }
    }
  })

  const restoreVersion = (filename, versionId) => run(filename, 'restore', async () => {
    try {
      const response = await check(
        await dashboardFetch(`/api/notebooks/${encodeURIComponent(filename)}/versions/${encodeURIComponent(versionId)}/restore`, { method: 'POST' }),
        'Restore failed',
      )
      const item = (versions[filename]?.items || []).find((v) => v.version_id === versionId)
      setPendingRestore((current) => ({ ...current, [filename]: null }))
      loadVersions(filename)
      setReload((n) => n + 1)
      const files = item && typeof item.companion_files === 'number' ? ` with ${item.companion_files} data file${item.companion_files === 1 ? '' : 's'}` : ''
      return { text: `Restored the version saved ${item ? new Date(item.saved_at).toLocaleString() : versionId}${files}`, audit: sequenceOf(response) }
    } catch (err) {
      return explain(err, filename)
    }
  })


  const compileApp = (filename) => run(filename, 'compile', async () => {
    setDiagnostics((current) => ({ ...current, [filename]: undefined })) // never show the last compile's findings for this one
    const response = await check(
      await dashboardFetch('/api/compile', jsonInit({ notebook_path: filename, per_notebook: true })),
      'Compilation failed',
    )
    const data = await response.json()
    // Kept per notebook: another notebook's compile never replaces or mixes into these.
    setDiagnostics((current) => ({ ...current, [filename]: diagnosticsFrom(data) }))
    setReload((n) => n + 1)
    return 'Compiled its own app'
  })

  const downloadApp = (filename) => run(filename, 'download', async () => {
    const response = await check(await dashboardFetch('/api/download?notebook=' + encodeURIComponent(filename)), 'Download failed')
    saveBlob(await response.blob(), stem(filename) + '-app.zip')
    return 'Downloaded the app'
  })

  const exportOpenapi = (filename) => run(filename, 'openapi', async () => {
    const response = await check(await dashboardFetch('/api/export-openapi', jsonInit({ notebook: filename, format: 'json' })), 'Export failed')
    const data = await response.json()
    const text = typeof data.content === 'string' ? data.content : JSON.stringify(data.schema ?? {}, null, 2)
    saveBlob(new Blob([text], { type: 'application/json' }), stem(filename) + '-openapi.json')
    return 'Exported the OpenAPI schema'
  })

  const exportSdk = (filename, language) => run(filename, 'sdk-' + language, async () => {
    const response = await check(await dashboardFetch('/api/export-sdk', jsonInit({ notebook: filename, language })), 'SDK export failed')
    const data = await response.json()
    const extension = language === 'typescript' ? 'ts' : 'py'
    saveBlob(new Blob([data.code ?? ''], { type: 'text/plain' }), `${stem(filename)}-client.${extension}`)
    return `Exported the ${language} client`
  })

  const deployApp = (filename) => run(filename, 'deploy', async () => {
    const response = await check(await dashboardFetch('/api/deploy', jsonInit({ notebook: filename })), 'Deployment failed')
    const data = await response.json()
    return 'Built image ' + (data.tag || '(unnamed)')
  })

  const eventNode = event && (
    <p role={event.error ? 'alert' : 'status'} data-testid="library-event"
      className={'mb-2 text-sm ' + (event.error ? 'text-red-400' : 'text-emerald-400')}>
      {event.error ? '❌ ' : '✅ '}{event.text}
      {event.audit && (
        <>
          {' '}
          <a href="#governance-audit" className="underline text-blue-300" data-testid="lifecycle-audit-link"
            onClick={() => onAudit && onAudit(event.audit)}>View audit record #{event.audit}</a>
        </>
      )}
    </p>
  )

  if (error) return <p className="text-sm text-red-400">❌ {error}</p>
  if (notebooks === null) return <p className="text-sm text-slate-400">Loading notebooks...</p>
  if (notebooks.length === 0) return <>{eventNode}<p className="text-sm text-slate-400">No notebooks uploaded yet</p></>

  const button = 'text-xs text-blue-400 hover:text-blue-300 disabled:text-slate-600'

  return (
    <>
    {eventNode}
    <ul className="space-y-2">
      {notebooks.map((notebook) => {
        const app = notebook.per_notebook_app
        const filename = notebook.filename
        const working = busy[filename]
        const note = notes[filename]
        return (
          <li key={filename} className="bg-slate-900 rounded px-3 py-2">
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <code className="text-sm text-slate-200 break-all">{filename}</code>
                <span className="ml-2 text-xs text-slate-500">{formatSize(notebook.size_bytes || 0)}</span>
                {notebook.currently_compiled && <span className="ml-2 text-xs text-emerald-400">compiled</span>}
                {app && app.status === 'compiled' && (
                  <span className="ml-2 text-xs text-emerald-400">
                    {app.notebook_changed_since_compile ? 'app out of date' : 'app ready'}
                  </span>
                )}
                {app && app.status === 'incomplete' && <span className="ml-2 text-xs text-amber-400">app incomplete</span>}
              </div>
              <button type="button" onClick={() => onOpen({ filename })} className="ml-3 text-sm text-blue-400 hover:text-blue-300">
                Open
              </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-3">
              <button type="button" className={button} disabled={Boolean(working) || !can('compile')} title={refusal('compile')} onClick={() => compileApp(filename)}>
                {working === 'compile' ? 'Compiling...' : app ? 'Recompile app' : 'Compile app'}
              </button>
              {app && app.status === 'compiled' && (
                <>
                  <button type="button" className={button} disabled={Boolean(working)} onClick={() => downloadApp(filename)}>
                    {working === 'download' ? 'Downloading...' : 'Download app'}
                  </button>
                  <button type="button" className={button} disabled={Boolean(working)} onClick={() => exportOpenapi(filename)}>
                    OpenAPI
                  </button>
                  <button type="button" className={button} disabled={Boolean(working)} onClick={() => exportSdk(filename, 'python')}>
                    Python SDK
                  </button>
                  <button type="button" className={button} disabled={Boolean(working)} onClick={() => exportSdk(filename, 'typescript')}>
                    TypeScript SDK
                  </button>
                  <button
                    type="button"
                    className={button}
                    disabled={Boolean(working) || !can('deploy')}
                    title={refusal('deploy')}
                    onClick={() => {
                      if (window.confirm('Build a Docker image for ' + filename + '?')) deployApp(filename)
                    }}
                  >
                    {working === 'deploy' ? 'Deploying...' : 'Deploy'}
                  </button>
                </>
              )}
              {[['rename', 'Rename'], ['versions', 'Versions'], ['delete', 'Delete']].map(([name, label]) => (
                <button key={name} type="button" className={button + (name === 'delete' ? ' !text-red-400' : '')}
                  ref={(element) => { triggers.current[`${filename}|${name}`] = element }}
                  aria-label={`${label} ${filename}`} aria-expanded={panel[filename] === name}
                  disabled={Boolean(working) || (name === 'rename' && !can('rename')) || (name === 'delete' && !can('delete'))}
                  title={name === 'rename' || name === 'delete' ? refusal(name) : undefined}
                  onClick={() => (panel[filename] === name ? closePanel(filename, name) : openPanel(filename, name))}>
                  {label}
                </button>
              ))}
            </div>
            {note && (
              <p role={note.kind === 'error' ? 'alert' : 'status'} data-testid={'note-' + filename}
                className={'mt-1 text-xs ' + (note.kind === 'error' ? 'text-red-400' : 'text-emerald-400')}>
                {note.kind === 'error' ? '❌ ' : '✅ '}{note.text}
                {note.audit && (
                  <>
                    {' '}
                    <a href="#governance-audit" className="underline text-blue-300" data-testid="lifecycle-audit-link"
                      onClick={() => onAudit && onAudit(note.audit)}>View audit record #{note.audit}</a>
                  </>
                )}
              </p>
            )}
            {panel[filename] === 'rename' && (
              <form role="group" aria-label={'Rename ' + filename} className="mt-2 flex items-center gap-2"
                onSubmit={(event) => { event.preventDefault(); renameNotebook(filename) }}
                onKeyDown={(event) => { if (event.key === 'Escape') closePanel(filename, 'rename') }}>
                <label className="text-xs text-slate-300" htmlFor={'rename-first-' + filename}>New name</label>
                <input id={'rename-first-' + filename} className="bg-slate-800 text-slate-100 text-sm rounded px-2 py-1"
                  value={renameTo[filename] ?? filename} onChange={(event) => setRenameTo((c) => ({ ...c, [filename]: event.target.value }))} />
                <button type="submit" className={button} disabled={Boolean(working)}>{working === 'rename' ? 'Renaming...' : 'Rename'}</button>
                <button type="button" className={button} onClick={() => closePanel(filename, 'rename')}>Cancel</button>
              </form>
            )}
            {panel[filename] === 'delete' && (
              <div role="alertdialog" aria-label={'Delete ' + filename} className="mt-2 text-xs text-slate-200"
                onKeyDown={(event) => { if (event.key === 'Escape') closePanel(filename, 'delete') }}>
                <p>Delete <code>{filename}</code>? It moves to the trash with its saved versions, data files and compiled app, and can be restored from Trash until it expires.</p>
                <div className="mt-1 flex gap-3">
                  <button type="button" id={'delete-first-' + filename} className="text-xs text-slate-300 hover:text-white" onClick={() => closePanel(filename, 'delete')}>Cancel</button>
                  <button type="button" className="text-xs text-red-400 hover:text-red-300" disabled={Boolean(working)} onClick={() => deleteNotebook(filename)}>
                    {working === 'delete' ? 'Deleting...' : 'Yes, delete ' + filename}
                  </button>
                </div>
              </div>
            )}
            {panel[filename] === 'versions' && (
              <div role="region" aria-label={'Versions of ' + filename} className="mt-2 text-xs text-slate-200"
                onKeyDown={(event) => { if (event.key === 'Escape') closePanel(filename, 'versions') }}>
                <button type="button" id={'versions-first-' + filename} className={button} onClick={() => closePanel(filename, 'versions')}>Close versions</button>
                {versions[filename]?.status === 'loading' && <p className="text-slate-400">Loading versions...</p>}
                {versions[filename]?.status === 'error' && <p role="alert" className="text-red-400">❌ {versions[filename].error}</p>}
                {versions[filename]?.status === 'ready' && versions[filename].items.length === 0 && (
                  <p className="text-slate-400">No earlier versions: a version is saved each time this notebook is overwritten.</p>
                )}
                {versions[filename]?.status === 'ready' && versions[filename].items.length > 0 && (
                  <ul className="mt-1 space-y-1">
                    {versions[filename].items.map((version) => (
                      <li key={version.version_id} data-testid={'version-' + filename} className="flex items-center gap-3">
                        <span>{new Date(version.saved_at).toLocaleString()}</span>
                        <span className="text-slate-500">{formatSize(version.size_bytes || 0)}</span>
                        {typeof version.companion_files === 'number' && version.companion_files > 0 && (
                          <span className="text-slate-500">{version.companion_files} data file{version.companion_files === 1 ? '' : 's'}</span>
                        )}
                        {pendingRestore[filename] === version.version_id ? (
                          <>
                            <button type="button" className="text-xs text-amber-400 hover:text-amber-300" disabled={Boolean(working)}
                              onClick={() => restoreVersion(filename, version.version_id)}>
                              {working === 'restore' ? 'Restoring...' : 'Confirm restore'}
                            </button>
                            <button type="button" className={button} onClick={() => setPendingRestore((c) => ({ ...c, [filename]: null }))}>Cancel</button>
                          </>
                        ) : (
                          <button type="button" className={button} disabled={Boolean(working)}
                            aria-label={`Restore the version of ${filename} saved ${new Date(version.saved_at).toLocaleString()}`}
                            onClick={() => setPendingRestore((c) => ({ ...c, [filename]: version.version_id }))}>
                            Restore
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {versions[filename]?.status === 'ready' && versions[filename].items.length > 0 && (
                  <VersionCompare filename={filename} versions={versions[filename].items} />
                )}
              </div>
            )}
            {diagnostics[filename] && (
              <div className="mt-2" data-testid={'diagnostics-' + filename}>
                <Diagnostics diagnostics={diagnostics[filename]} />
              </div>
            )}
          </li>
        )
      })}
    </ul>
    {(trashCount > 0 || trash !== null) && (
      <button type="button" className={button + ' mt-3'} aria-expanded={trash !== null} onClick={toggleTrash}>
        {trash !== null ? 'Hide trash' : `Trash (${trashCount})`}
      </button>
    )}
    {trash !== null && (
      <section aria-label="Trash" className="mt-2" data-testid="trash">
        {trash.length === 0 && <p className="text-xs text-slate-400">The trash is empty.</p>}
        <ul className="space-y-1">
          {trash.map((item) => (
            <li key={item.trash_id} className="bg-slate-900 rounded px-3 py-1 flex items-center justify-between text-xs text-slate-300">
              <span><code>{item.filename}</code> <span className="text-slate-500">expires {new Date(item.expires_at).toLocaleDateString()}</span></span>
              <button type="button" className={button} disabled={Boolean(busy[item.filename]) || !can('restore')} title={refusal('restore')}
                aria-label={'Restore ' + item.filename + ' from the trash'} onClick={() => restoreFromTrash(item)}>
                {busy[item.filename] === 'untrash' ? 'Restoring...' : 'Restore'}
              </button>
            </li>
          ))}
        </ul>
      </section>
    )}
    </>
  )
}
