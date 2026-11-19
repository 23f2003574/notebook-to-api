import { useEffect, useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'

const formatSize = (bytes) => (bytes >= 1024 ? (bytes / 1024).toFixed(1) + ' KB' : bytes + ' B')

// Notebooks already stored on the dashboard (GET /api/notebooks), so an earlier upload can be
// opened again -- inspected and compiled -- without uploading the file a second time.
export default function NotebookLibrary({ onOpen, refreshKey }) {
  const [notebooks, setNotebooks] = useState(null)
  const [error, setError] = useState(null)

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
  }, [refreshKey])

  if (error) return <p className="text-sm text-red-400">❌ {error}</p>
  if (notebooks === null) return <p className="text-sm text-slate-400">Loading notebooks...</p>
  if (notebooks.length === 0) return <p className="text-sm text-slate-400">No notebooks uploaded yet</p>

  return (
    <ul className="space-y-2">
      {notebooks.map((notebook) => (
        <li key={notebook.filename} className="flex items-center justify-between bg-slate-900 rounded px-3 py-2">
          <div className="min-w-0">
            <code className="text-sm text-slate-200 break-all">{notebook.filename}</code>
            <span className="ml-2 text-xs text-slate-500">{formatSize(notebook.size_bytes || 0)}</span>
            {notebook.currently_compiled && (
              <span className="ml-2 text-xs text-emerald-400">compiled</span>
            )}
          </div>
          <button
            type="button"
            onClick={() => onOpen({ filename: notebook.filename })}
            className="ml-3 text-sm text-blue-400 hover:text-blue-300"
          >
            Open
          </button>
        </li>
      ))}
    </ul>
  )
}
