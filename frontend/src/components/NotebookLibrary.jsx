import { useCallback, useEffect, useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
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
export default function NotebookLibrary({ onOpen, refreshKey }) {
  const [notebooks, setNotebooks] = useState(null)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState({}) // filename -> action name
  const [notes, setNotes] = useState({}) // filename -> { kind: 'ok' | 'error', text }
  const [diagnostics, setDiagnostics] = useState({}) // filename -> what its last compile reported

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
      } catch (err) {
        if (!controller.signal.aborted) setError(err.message)
      }
    })()
    return () => controller.abort()
  }, [refreshKey, reload])

  // One action on one notebook; every other row keeps its own state.
  const run = useCallback(async (filename, action, work) => {
    setBusy((current) => ({ ...current, [filename]: action }))
    setNotes((current) => ({ ...current, [filename]: null }))
    try {
      const text = await work()
      setNotes((current) => ({ ...current, [filename]: { kind: 'ok', text } }))
    } catch (err) {
      setNotes((current) => ({ ...current, [filename]: { kind: 'error', text: err.message } }))
    } finally {
      setBusy((current) => {
        const next = { ...current }
        delete next[filename]
        return next
      })
    }
  }, [])

  const check = async (response, fallback) => {
    if (!response.ok) throw new Error(await errorFromResponse(response, fallback))
    return response
  }

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

  if (error) return <p className="text-sm text-red-400">❌ {error}</p>
  if (notebooks === null) return <p className="text-sm text-slate-400">Loading notebooks...</p>
  if (notebooks.length === 0) return <p className="text-sm text-slate-400">No notebooks uploaded yet</p>

  const button = 'text-xs text-blue-400 hover:text-blue-300 disabled:text-slate-600'

  return (
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
              <button type="button" className={button} disabled={Boolean(working)} onClick={() => compileApp(filename)}>
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
                    disabled={Boolean(working)}
                    onClick={() => {
                      if (window.confirm('Build a Docker image for ' + filename + '?')) deployApp(filename)
                    }}
                  >
                    {working === 'deploy' ? 'Deploying...' : 'Deploy'}
                  </button>
                </>
              )}
            </div>
            {note && (
              <p className={'mt-1 text-xs ' + (note.kind === 'error' ? 'text-red-400' : 'text-emerald-400')}>
                {note.kind === 'error' ? '❌ ' : '✅ '}{note.text}
              </p>
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
  )
}
