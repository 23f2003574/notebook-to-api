import { useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'

const link = 'text-xs text-blue-400 hover:text-blue-300 disabled:opacity-50'
const base = (filename) => `/api/notebooks/${encodeURIComponent(filename)}/versions`

// Note editing and deletion for one saved version of a notebook. Both are admin actions on the
// backend; `canEdit`/`canDelete` only reflect the key's scope so the controls are disabled up front.
export function VersionActions({ filename, version, canEdit = true, canDelete = true, refusal = '', onChanged }) {
  const [mode, setMode] = useState('idle') // idle | editing | deleting
  const [draft, setDraft] = useState(version.note || '')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  const call = async (work, fallback) => {
    setBusy(true)
    setMessage(null)
    try {
      const response = await work()
      if (!response.ok) throw new Error(await errorFromResponse(response, fallback))
      return true
    } catch (err) {
      setMessage({ kind: 'error', text: err.message })
      return false
    } finally {
      setBusy(false)
    }
  }

  const saveNote = async () => {
    const ok = await call(() => dashboardFetch(`${base(filename)}/${encodeURIComponent(version.version_id)}/note`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ note: draft }),
    }), 'Could not save the note')
    if (ok) { setMode('idle'); setMessage({ kind: 'ok', text: 'Note saved' }); onChanged?.() }
  }

  const remove = async () => {
    const ok = await call(() => dashboardFetch(`${base(filename)}/${encodeURIComponent(version.version_id)}`, { method: 'DELETE' }), 'Could not delete the version')
    if (ok) onChanged?.()
  }

  const when = new Date(version.saved_at).toLocaleString()
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      {version.note && mode !== 'editing' && <span className="text-slate-300 italic" data-testid="version-note">“{version.note}”</span>}
      {mode === 'editing' ? (
        <form className="inline-flex items-center gap-2" aria-label={`Edit the note of the version saved ${when}`}
          onSubmit={(event) => { event.preventDefault(); saveNote() }}>
          <input aria-label={`Note for the version saved ${when}`} value={draft} maxLength={500} onChange={(e) => setDraft(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded px-2 py-0.5" />
          <button type="submit" className={link} disabled={busy}>{busy ? 'Saving...' : 'Save note'}</button>
          <button type="button" className={link} onClick={() => { setMode('idle'); setDraft(version.note || '') }}>Cancel</button>
        </form>
      ) : (
        <button type="button" className={link} disabled={!canEdit || busy} title={canEdit ? undefined : refusal}
          aria-label={`Edit the note of the version saved ${when}`} onClick={() => setMode('editing')}>
          {version.note ? 'Edit note' : 'Add note'}
        </button>
      )}
      {mode === 'deleting' ? (
        <span role="alertdialog" aria-label={`Delete the version saved ${when}`} className="inline-flex items-center gap-2">
          <span>Delete this version permanently?</span>
          <button type="button" className="text-xs text-red-400 hover:text-red-300" disabled={busy} onClick={remove}>{busy ? 'Deleting...' : 'Yes, delete version'}</button>
          <button type="button" className={link} onClick={() => setMode('idle')}>Cancel</button>
        </span>
      ) : (
        <button type="button" className={link + ' !text-red-400'} disabled={!canDelete || busy} title={canDelete ? undefined : refusal}
          aria-label={`Delete the version saved ${when}`} onClick={() => setMode('deleting')}>Delete</button>
      )}
      {message && <span role={message.kind === 'error' ? 'alert' : 'status'} className={message.kind === 'error' ? 'text-red-400' : 'text-emerald-400'}>{message.text}</span>}
    </span>
  )
}

// Export a notebook with its whole version history as one .zip, and restore from such an archive
// through the backend's all-or-nothing import (a rejected archive leaves the notebook untouched).
export function VersionArchive({ filename, canImport = true, refusal = '', onImported }) {
  const [file, setFile] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  const exportArchive = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const response = await dashboardFetch(`${base(filename)}/export`)
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not export the versions'))
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${filename.replace(/\.ipynb$/, '')}-versions.zip`
      anchor.click()
      URL.revokeObjectURL(url)
      setMessage({ kind: 'ok', text: 'Exported the notebook with its version history' })
    } catch (err) {
      setMessage({ kind: 'error', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  const importArchive = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const response = await dashboardFetch(`${base(filename)}/import?overwrite=true`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not import the archive'))
      setConfirming(false)
      setFile(null)
      setMessage({ kind: 'ok', text: 'Imported the archive: the notebook and its versions were replaced' })
      onImported?.()
    } catch (err) {
      setConfirming(false)
      setMessage({ kind: 'error', text: `${err.message} The notebook was not changed.` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 text-xs" role="group" aria-label={`Version archive of ${filename}`}>
      <button type="button" className={link} disabled={busy} onClick={exportArchive}>Export versions (.zip)</button>
      <label className="ml-4 text-slate-300">
        Import archive{' '}
        <input type="file" accept=".zip,application/zip" disabled={!canImport || busy} title={canImport ? undefined : refusal}
          onChange={(event) => { setFile(event.target.files?.[0] || null); setConfirming(false); setMessage(null) }} />
      </label>
      {file && !confirming && <button type="button" className={link + ' ml-2'} disabled={!canImport || busy} onClick={() => setConfirming(true)}>Import {file.name}</button>}
      {confirming && (
        <span role="alertdialog" aria-label="Confirm archive import" className="ml-2">
          This replaces {filename} and its entire version history.{' '}
          <button type="button" className="text-red-400 hover:text-red-300" disabled={busy} onClick={importArchive}>{busy ? 'Importing...' : 'Yes, import'}</button>{' '}
          <button type="button" className={link} onClick={() => setConfirming(false)}>Cancel</button>
        </span>
      )}
      {message && <p role={message.kind === 'error' ? 'alert' : 'status'} className={'mt-1 ' + (message.kind === 'error' ? 'text-red-400' : 'text-emerald-400')}>{message.text}</p>}
    </div>
  )
}
