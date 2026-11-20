import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { useState } from 'react'

export default function NotebookUpload({ onSuccess }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [uploaded, setUploaded] = useState(null)
  const [importSummary, setImportSummary] = useState(null)

  // The one upload path: used for a file chosen with the picker and for a dropped file.
  const uploadFile = async (file) => {
    const isArchive = file.name.toLowerCase().endsWith('.zip')
    if (!isArchive && !file.name.endsWith('.ipynb')) {
      setError('❌ Please upload a .ipynb file or a .zip archive of notebooks')
      return
    }

    setLoading(true)
    setError(null)
    setUploaded(null)
    setImportSummary(null)

    try {
      const formData = new FormData()
      formData.append('file', file)

      if (isArchive) {
        // An archive can carry data and model files next to its notebooks; the server stores them
        // with each notebook and says what it kept and what it refused.
        const response = await dashboardFetch('/api/notebooks/import', { method: 'POST', body: formData })
        if (!response.ok) throw new Error(await errorFromResponse(response, 'Import failed'))
        const data = await response.json()
        const results = Array.isArray(data.results) ? data.results : []
        setImportSummary(results)
        const first = results.find((r) => r.status === 'success')
        if (first) {
          setUploaded(first.filename)
          onSuccess({ filename: first.filename })
        } else {
          setError('❌ No notebook was imported from the archive')
        }
        return
      }

      const response = await dashboardFetch('/api/upload', {
        method: 'POST',
        body: formData
      })

      if (!response.ok) throw new Error(await errorFromResponse(response, 'Upload failed'))

      const data = await response.json()
      setUploaded(file.name)
      onSuccess(data)
    } catch (err) {
      setError('❌ ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleFileUpload = async (event) => {
    const file = event.target.files?.[0]
    if (!file || loading) return
    try {
      await uploadFile(file)
    } finally {
      event.target.value = ''
    }
  }

  const allowDrop = (event) => {
    // Without this the browser would open the dropped file instead of handing it to us.
    event.preventDefault()
    if (!loading) setDragging(true)
  }

  const handleDragLeave = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false)
  }

  const handleDrop = async (event) => {
    event.preventDefault()
    setDragging(false)
    if (loading) return

    const files = event.dataTransfer?.files
    if (!files || files.length === 0) {
      setError('❌ No file was dropped')
    } else if (files.length > 1) {
      setError('❌ Drop one notebook at a time')
    } else {
      await uploadFile(files[0])
    }
  }

  return (
    <div>
      <label
        data-dragging={dragging}
        onDragEnter={allowDrop}
        onDragOver={allowDrop}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`flex flex-col items-center justify-center w-full p-8 border-2 border-dashed rounded-lg cursor-pointer hover:bg-slate-700/50 transition focus-within:ring-2 focus-within:ring-emerald-500 ${
          dragging ? 'border-emerald-400 bg-slate-700/50' : 'border-slate-600'
        }`}
      >
        <div className="flex flex-col items-center justify-center">
          <span className="text-2xl mb-2" aria-hidden="true">📁</span>
          <span className="text-white font-medium">Click to upload or drag & drop</span>
          <span className="text-xs text-slate-400 mt-1">.ipynb notebooks, or a .zip with their data files</span>
        </div>
        <input
          type="file"
          accept=".ipynb,.zip"
          onChange={handleFileUpload}
          // Not `disabled`: a disabled control drops keyboard focus mid-upload. The picker is
          // blocked instead, so focus stays on the control while it is busy.
          onClick={(event) => { if (loading) event.preventDefault() }}
          aria-disabled={loading}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'notebook-upload-error' : undefined}
          className="sr-only"
        />
      </label>
      <div role="status">
        {loading && <p className="mt-3 text-slate-400">⏳ Uploading...</p>}
        {uploaded && !loading && !error && <p className="mt-3 text-emerald-400 text-sm">✅ Uploaded {uploaded}</p>}
      </div>
      {importSummary && (
        <ul className="mt-3 space-y-2 text-sm" data-testid="import-summary">
          {importSummary.map((r, i) => (
            <li key={`${r.filename}-${i}`} className="text-slate-300">
              {r.status === 'success' ? '✅' : '❌'} <code>{r.filename}</code>
              {r.status !== 'success' && r.error ? `: ${r.error}` : ''}
              {Array.isArray(r.companion_files) && r.companion_files.length > 0 && (
                <span className="block text-slate-400">
                  Data files kept: {r.companion_files.map((f) => f.path).join(', ')}
                </span>
              )}
              {Array.isArray(r.companion_skipped) && r.companion_skipped.map((f) => (
                <span key={f.path} className="block text-amber-300">⚠ Skipped <code>{f.path}</code>: {f.reason}</span>
              ))}
            </li>
          ))}
        </ul>
      )}
      {error && <p id="notebook-upload-error" role="alert" className="mt-3 text-red-400 text-sm">{error}</p>}
    </div>
  )
}
