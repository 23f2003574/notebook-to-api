import { useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { metadataProblem } from '../notebookMetadata'

const putJson = (path, body) => dashboardFetch(path, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// Edits one notebook's tags and description (PUT /api/notebooks/{f}/tags and /description). The row
// is updated by `onSaved` only after the server accepted the change.
export default function NotebookMetadata({ notebook, limits, canEdit, refusal, onSaved, onClose }) {
  const filename = notebook.filename
  const [tags, setTags] = useState(Array.isArray(notebook.tags) ? notebook.tags : [])
  const [tagInput, setTagInput] = useState('')
  const [description, setDescription] = useState(notebook.description || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const addTag = () => {
    const tag = tagInput.trim()
    if (!tag) return
    if (tags.includes(tag)) {
      setError(`"${tag}" is already a tag`)
      return
    }
    setTags([...tags, tag])
    setTagInput('')
    setError(null)
  }

  const problem = metadataProblem(tags, description, limits)
  const changed = JSON.stringify(tags) !== JSON.stringify(notebook.tags || []) || description !== (notebook.description || '')

  const save = async (event) => {
    event.preventDefault()
    if (problem || !changed) return
    setSaving(true)
    setError(null)
    const saved = { tags: notebook.tags || [], description: notebook.description || '' }
    try {
      if (JSON.stringify(tags) !== JSON.stringify(notebook.tags || [])) {
        const response = await putJson(`/api/notebooks/${encodeURIComponent(filename)}/tags`, { tags })
        if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not save the tags'))
        saved.tags = (await response.json()).tags ?? tags
      }
      if (description !== (notebook.description || '')) {
        const response = await putJson(`/api/notebooks/${encodeURIComponent(filename)}/description`, { description })
        if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not save the description'))
        saved.description = (await response.json()).description ?? description
      }
      onSaved(saved)
    } catch (err) {
      setError(err.message)
      onSaved(saved) // whatever did persist before the failure is shown as saved
    } finally {
      setSaving(false)
    }
  }

  return (
    <form role="group" aria-label={`Details of ${filename}`} className="mt-2 text-xs text-slate-200 space-y-2"
      onSubmit={save} onKeyDown={(event) => { if (event.key === 'Escape') onClose() }}>
      <div>
        <label htmlFor={`details-first-${filename}`} className="block text-slate-300">Tags</label>
        <ul className="my-1 flex flex-wrap gap-1">
          {tags.map((tag) => (
            <li key={tag} className="bg-slate-800 rounded px-2 py-0.5">
              {tag}{' '}
              <button type="button" aria-label={`Remove tag ${tag} from ${filename}`} disabled={!canEdit || saving}
                className="text-slate-400 hover:text-white" onClick={() => setTags(tags.filter((t) => t !== tag))}>×</button>
            </li>
          ))}
        </ul>
        <input id={`details-first-${filename}`} className="bg-slate-800 text-slate-100 rounded px-2 py-1" value={tagInput}
          disabled={!canEdit || saving} placeholder="Add a tag"
          onChange={(event) => setTagInput(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addTag() } }} />
        <button type="button" className="ml-2 text-blue-400 hover:text-blue-300" disabled={!canEdit || saving} onClick={addTag}>Add tag</button>
      </div>
      <div>
        <label htmlFor={`description-${filename}`} className="block text-slate-300">Description</label>
        <textarea id={`description-${filename}`} className="w-full bg-slate-800 text-slate-100 rounded px-2 py-1" rows={2}
          value={description} disabled={!canEdit || saving} onChange={(event) => setDescription(event.target.value)} />
        {limits?.max_description_length ? <span className="text-slate-500">{description.length}/{limits.max_description_length}</span> : null}
      </div>
      {!canEdit && <p className="text-amber-300">{refusal || 'Your key cannot edit tags or descriptions'}</p>}
      {problem && <p role="alert" className="text-red-400">❌ {problem}</p>}
      {error && <p role="alert" className="text-red-400">❌ {error}</p>}
      <div className="flex gap-3">
        <button type="submit" className="text-blue-400 hover:text-blue-300 disabled:opacity-50" disabled={!canEdit || saving || !changed || Boolean(problem)}>
          {saving ? 'Saving...' : 'Save details'}
        </button>
        <button type="button" className="text-slate-300 hover:text-white" onClick={onClose}>Close</button>
      </div>
    </form>
  )
}
