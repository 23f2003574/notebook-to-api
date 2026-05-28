import { useState } from 'react'

export default function NotebookUpload({ onSuccess }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const handleFileUpload = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return

    if (!file.name.endsWith('.ipynb')) {
      setError('❌ Please upload a .ipynb file')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch('http://localhost:8001/api/upload', {
        method: 'POST',
        body: formData
      })

      if (!response.ok) throw new Error('Upload failed')

      const data = await response.json()
      onSuccess(data)
    } catch (err) {
      setError('❌ ' + err.message)
    } finally {
      setLoading(false)
      event.target.value = ''
    }
  }

  return (
    <div>
      <label className="flex flex-col items-center justify-center w-full p-8 border-2 border-dashed border-slate-600 rounded-lg cursor-pointer hover:bg-slate-700/50 transition">
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
          className="hidden"
        />
      </label>
      {loading && <p className="mt-3 text-slate-400">⏳ Uploading...</p>}
      {error && <p className="mt-3 text-red-400 text-sm">{error}</p>}
    </div>
  )
}
