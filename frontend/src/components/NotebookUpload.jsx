import { dashboardApiUrl, dashboardHeaders } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { useState } from 'react'

export default function NotebookUpload({ onSuccess }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(false)

  // The one upload path: used for a file chosen with the picker and for a dropped file.
  const uploadFile = async (file) => {
    if (!file.name.endsWith('.ipynb')) {
      setError('❌ Please upload a .ipynb file')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch(dashboardApiUrl('/api/upload'), {
        method: 'POST',
        headers: dashboardHeaders(),
        body: formData
      })

      if (!response.ok) throw new Error(await errorFromResponse(response, 'Upload failed'))

      const data = await response.json()
      onSuccess(data)
    } catch (err) {
      setError('❌ ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleFileUpload = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
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
          <span className="text-2xl mb-2">📁</span>
          <span className="text-white font-medium">Click to upload or drag & drop</span>
          <span className="text-xs text-slate-400 mt-1">.ipynb files only</span>
        </div>
        <input
          type="file"
          accept=".ipynb"
          onChange={handleFileUpload}
          disabled={loading}
          className="sr-only"
        />
      </label>
      {loading && <p className="mt-3 text-slate-400">⏳ Uploading...</p>}
      {error && <p className="mt-3 text-red-400 text-sm">{error}</p>}
    </div>
  )
}
