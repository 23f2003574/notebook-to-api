import { useEffect, useRef, useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'

const FILES = {
  dockerfile: { label: 'Dockerfile', name: 'Dockerfile', field: 'dockerfile', path: (nb) => '/api/dockerfile-preview' + (nb ? `?notebook_path=${encodeURIComponent(nb)}` : '') },
  compose: { label: 'Docker Compose', name: 'docker-compose.yml', field: 'docker_compose', path: () => '/api/docker-compose-preview' },
  kubernetes: { label: 'Kubernetes', name: 'kubernetes.yaml', field: 'kubernetes_manifest', path: () => '/api/k8s-preview' },
  env: { label: 'Environment', name: '.env.example', field: 'env_example', path: () => '/api/env-example-preview' },
}

// Read-only preview of the deployment files a compile would write, taken from the backend's own preview
// routes (nothing is generated here). Contents are rendered as text in a <pre>, never as HTML.
export default function DeploymentPreview({ notebook = null }) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState('dockerfile')
  const [files, setFiles] = useState({})
  const [note, setNote] = useState(null)
  const notebookKey = notebook || ''
  const previous = useRef(notebookKey)

  useEffect(() => {
    if (previous.current !== notebookKey) {
      previous.current = notebookKey
      setFiles({}) // the Dockerfile depends on the notebook
    }
  }, [notebookKey])

  const entry = files[tab]

  useEffect(() => {
    if (!open || entry) return undefined
    const controller = new AbortController()
    const spec = FILES[tab]
    ;(async () => {
      try {
        const response = await dashboardFetch(spec.path(notebook), { signal: controller.signal })
        if (!response.ok) throw new Error(await errorFromResponse(response, `Could not load the ${spec.label} preview`))
        const data = await response.json()
        const text = typeof data[spec.field] === 'string' ? data[spec.field] : null
        if (!controller.signal.aborted) setFiles((f) => ({ ...f, [tab]: text === null ? { error: `The backend returned no ${spec.label}.` } : { text } }))
      } catch (err) {
        if (!controller.signal.aborted) setFiles((f) => ({ ...f, [tab]: { error: err.message } }))
      }
    })()
    return () => controller.abort()
  }, [open, tab, entry, notebook])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(entry.text)
      setNote({ kind: 'ok', text: `Copied ${FILES[tab].name}` })
    } catch {
      setNote({ kind: 'error', text: 'Could not copy: select the text and copy it manually.' })
    }
  }

  const download = () => {
    const url = URL.createObjectURL(new Blob([entry.text], { type: 'text/plain' }))
    const link = document.createElement('a')
    link.href = url
    link.download = FILES[tab].name
    link.click()
    URL.revokeObjectURL(url)
    setNote({ kind: 'ok', text: `Downloaded ${FILES[tab].name}` })
  }

  return (
    <section aria-label="Deployment file preview">
      <button type="button" aria-expanded={open} className="text-sm text-blue-400 hover:text-blue-300" onClick={() => setOpen(!open)}>
        {open ? 'Hide deployment file preview' : 'Preview deployment files'}
      </button>
      {open && (
        <div className="mt-3 text-sm text-slate-200">
          <div role="tablist" aria-label="Deployment file" className="flex gap-3">
            {Object.entries(FILES).map(([key, spec]) => (
              <button key={key} role="tab" type="button" aria-selected={tab === key} aria-controls="deployment-preview-panel"
                className={tab === key ? 'text-white underline' : 'text-slate-400 hover:text-white'}
                onClick={() => { setTab(key); setNote(null) }}>{spec.label}</button>
            ))}
          </div>
          <div role="tabpanel" id="deployment-preview-panel" aria-label={FILES[tab].name} className="mt-2">
            {!entry && <p role="status" className="text-slate-400">Loading {FILES[tab].label}...</p>}
            {entry?.error && <p role="alert" className="text-red-400">❌ {entry.error}</p>}
            {entry?.text !== undefined && (
              <>
                <pre tabIndex={0} aria-label={`${FILES[tab].name} contents`} className="bg-slate-900 rounded p-3 text-xs overflow-auto max-h-96 whitespace-pre">{entry.text}</pre>
                <div className="mt-2 flex gap-3 text-xs">
                  <button type="button" className="text-blue-400 hover:text-blue-300" onClick={copy}>Copy</button>
                  <button type="button" className="text-blue-400 hover:text-blue-300" onClick={download}>Download {FILES[tab].name}</button>
                </div>
              </>
            )}
            {note && <p role={note.kind === 'error' ? 'alert' : 'status'} className={'mt-1 text-xs ' + (note.kind === 'error' ? 'text-red-400' : 'text-emerald-400')}>{note.text}</p>}
            <p className="mt-2 text-xs text-slate-400">This is what the next compile writes; nothing has been generated or changed.</p>
          </div>
        </div>
      )}
    </section>
  )
}
