import { useState } from 'react'
import NotebookUpload from './NotebookUpload'
import FunctionList from './FunctionList'
import CompilationLogs from './CompilationLogs'

export default function Dashboard() {
  const [uploadedNotebook, setUploadedNotebook] = useState(null)
  const [functions, setFunctions] = useState([])
  const [logs, setLogs] = useState([])
  const [isCompiling, setIsCompiling] = useState(false)

  const handleUploadSuccess = (response) => {
    setUploadedNotebook(response.filename)
    setLogs(['✅ Notebook uploaded successfully'])
  }

  const handleCompile = async () => {
    if (!uploadedNotebook) {
      setLogs(['❌ No notebook uploaded'])
      return
    }

    setIsCompiling(true)
    setLogs(['🔄 Starting compilation...'])

    try {
      const response = await fetch('http://localhost:8001/api/compile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notebook_path: uploadedNotebook })
      })

      const data = await response.json()
      
      if (response.ok) {
        setFunctions(data.functions || [])
        setLogs([
          '✅ Compilation successful',
          `📦 Generated ${data.functions?.length || 0} functions`,
          `📚 Dependencies: ${data.dependencies?.join(', ') || 'none'}`
        ])
      } else {
        setLogs(['❌ Compilation failed: ' + (data.error || 'Unknown error')])
      }
    } catch (error) {
      setLogs(['❌ Error: ' + error.message])
    } finally {
      setIsCompiling(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800">
      {/* Header */}
      <div className="border-b border-slate-700 bg-slate-900/50 backdrop-blur">
        <div className="max-w-7xl mx-auto px-6 py-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl font-bold text-white mb-2">
                📓 notebook-to-api
              </h1>
              <p className="text-slate-400">
                Transform Jupyter notebooks into production-ready APIs
              </p>
            </div>
            <div className="text-right text-sm text-slate-400">
              <p>Backend: <span className="text-emerald-400">Running</span></p>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left Column - Upload & Compile */}
          <div className="lg:col-span-2 space-y-6">
            {/* Upload Section */}
            <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 hover:border-slate-600 transition">
              <h2 className="text-xl font-semibold text-white mb-4">
                📤 Upload Notebook
              </h2>
              <NotebookUpload onSuccess={handleUploadSuccess} />
              {uploadedNotebook && (
                <p className="mt-4 text-sm text-emerald-400">
                  ✅ Uploaded: <code className="bg-slate-900 px-2 py-1 rounded">{uploadedNotebook}</code>
                </p>
              )}
            </div>

            {/* Compile Section */}
            <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 hover:border-slate-600 transition">
              <h2 className="text-xl font-semibold text-white mb-4">
                ⚡ Compile to API
              </h2>
              <button
                onClick={handleCompile}
                disabled={!uploadedNotebook || isCompiling}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-semibold py-3 px-6 rounded-lg transition"
              >
                {isCompiling ? '🔄 Compiling...' : '🚀 Compile Now'}
              </button>
            </div>

            {/* Functions Section */}
            {functions.length > 0 && (
              <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
                <h2 className="text-xl font-semibold text-white mb-4">
                  📋 Extracted Functions ({functions.length})
                </h2>
                <FunctionList functions={functions} />
              </div>
            )}
          </div>

          {/* Right Column - Logs */}
          <div className="lg:col-span-1">
            <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 sticky top-6">
              <h2 className="text-xl font-semibold text-white mb-4">
                📜 Logs
              </h2>
              <CompilationLogs logs={logs} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
