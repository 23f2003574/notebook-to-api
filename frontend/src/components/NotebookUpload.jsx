import { allows, dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { useState } from 'react'

export default function NotebookUpload({ onSuccess, caller = null }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [uploaded, setUploaded] = useState(null)
  const [importSummary, setImportSummary] = useState(null)
  // A same-name conflict waiting for the user's decision: { file, sha256 }.
  const [conflict, setConflict] = useState(null)
  const [replaced, setReplaced] = useState(null)
  const canOverwrite = allows(caller, 'overwrite')

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
    setReplaced(null)
    setConflict(null)
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

      if (response.status === 409 && file.name.endsWith('.ipynb')) {
        // Same name already exists (or differs only by case): ask before replacing anything.
        await offerReplacement(file, response)
        return
      }

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

  // The current content's hash is what makes the replacement safe: if the notebook changes while the
  // dialog is open, the server refuses and nothing is lost.
  const offerReplacement = async (file, conflictResponse) => {
    const current = await dashboardFetch('/api/notebooks/' + encodeURIComponent(file.name))
    const sha256 = current.ok ? current.headers.get('X-Content-SHA256') : null
    if (!sha256) throw new Error(await errorFromResponse(conflictResponse, 'A notebook with this name already exists'))
    setConflict({ file, sha256 })
  }

  const cancelReplacement = () => setConflict(null)

  const confirmReplacement = async () => {
    const { file, sha256 } = conflict
    setConflict(null)
    setLoading(true)
    setError(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      // The server's expected_sha256 verifies the bytes being uploaded; whether the notebook being replaced
      // is still the one the user saw is checked here, right before the request.
      const current = await dashboardFetch('/api/notebooks/' + encodeURIComponent(file.name))
      if (!current.ok || current.headers.get('X-Content-SHA256') !== sha256) {
        throw new Error(`${file.name} changed after you opened this dialog, so it was not replaced. Upload it again to review the new version.`)
      }
      const bytes = new Uint8Array(await file.arrayBuffer())
      const digest = await crypto.subtle.digest('SHA-256', bytes)
      const uploadHash = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
      const query = `?overwrite=true&expected_sha256=${uploadHash}`
      const response = await dashboardFetch('/api/upload' + query, { method: 'POST', body: formData })
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Replacing the notebook failed'))
      const data = await response.json()
      let version = null
      try {
        const versions = await dashboardFetch('/api/notebooks/' + encodeURIComponent(file.name) + '/versions')
        if (versions.ok) version = (await versions.json()).versions?.[0]?.version_id ?? null
      } catch {
        version = null
      }
      setUploaded(file.name)
      setReplaced({ filename: file.name, version })
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
      {replaced && !loading && !error && (
        <p className="mt-3 text-emerald-400 text-sm" data-testid="replaced-notice">
          ✅ Replaced <code>{replaced.filename}</code>.{' '}
          {replaced.version
            ? <>The previous content was saved as version <code>{replaced.version}</code> and can be restored from the library.</>
            : 'The previous content was saved in its version history.'}
        </p>
      )}
      {conflict && (
        <div role="alertdialog" aria-modal="false" aria-labelledby="replace-title" aria-describedby="replace-body"
          className="mt-3 rounded border border-amber-500 bg-slate-900 p-4 text-sm">
          <h3 id="replace-title" className="font-semibold text-amber-300">Replace existing notebook?</h3>
          <p id="replace-body" className="mt-1 text-slate-300">
            <code>{conflict.file.name}</code> already exists. Replacing it keeps the current content as a version you can restore.
          </p>
          {!canOverwrite && (
            <p className="mt-2 text-amber-300">Your key's {caller?.scope} scope cannot replace notebooks; an admin key is required.</p>
          )}
          <div className="mt-3 flex gap-2">
            <button type="button" onClick={confirmReplacement} disabled={!canOverwrite}
              title={canOverwrite ? undefined : `Your key's ${caller?.scope} scope cannot replace notebooks`}
              className="px-3 py-1 rounded bg-amber-600 text-white disabled:opacity-50">Replace</button>
            <button type="button" onClick={cancelReplacement} className="px-3 py-1 rounded bg-slate-700 text-white">Cancel</button>
          </div>
        </div>
      )}
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
