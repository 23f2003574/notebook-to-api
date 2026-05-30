import { useState } from 'react'
import NotebookUpload from './NotebookUpload'
import FunctionList from './FunctionList'
import CompilationLogs from './CompilationLogs'
import EndpointTester from "./EndpointTester"

export default function Dashboard() {
  const [uploadedNotebook, setUploadedNotebook] = useState(null)
  const [functions, setFunctions] = useState([])
  const [logs, setLogs] = useState([])
  const [isCompiling, setIsCompiling] = useState(false)
  const [lastCompiled, setLastCompiled] = useState(null)
  const [endpoints, setEndpoints] = useState([])

  const handleUploadSuccess = async (response) => {
    setUploadedNotebook(response.filename)
    setLogs(['✅ Notebook uploaded successfully', '🔍 Inspecting notebook...'])

    try {
      const inspectResponse = await fetch('http://localhost:8001/api/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notebook_path: response.filename })
      })

      const inspectData = await inspectResponse.json()

      if (inspectResponse.ok) {
        setFunctions(inspectData.functions || [])
        const generatedEndpoints = (inspectData.functions || []).map(func => '/' + func.name)
        setEndpoints(generatedEndpoints)
        setLogs([
          '✅ Upload successful',
          '📦 Found ' + (inspectData.functions?.length || 0) + ' functions',
          '🚀 Generated ' + generatedEndpoints.length + ' endpoints'
        ])
      } else {
        setLogs(['⚠️ Upload succeeded', '❌ Inspection failed'])
      }
    } catch (error) {
      setLogs(['⚠️ Upload succeeded', '❌ Inspection error: ' + error.message])
    }
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
          '📦 Generated ' + (data.functions?.length || 0) + ' functions',
          '📚 Dependencies: ' + (data.dependencies?.join(', ') || 'none')
        ])
        setLastCompiled(new Date().toLocaleTimeString())
      } else {
        setLogs(['❌ Compilation failed: ' + (data.error || 'Unknown error')])
      }
    } catch (error) {
      setLogs(['❌ Error: ' + error.message])
    } finally {
      setIsCompiling(false)
    }
  }

  const generatedFiles = [
    { icon: '🐍', name: 'app.py' },
    { icon: '📋', name: 'requirements.txt' },
    { icon: '🐳', name: 'Dockerfile' },
    { icon: '📖', name: 'openapi.json' },
    { icon: '📦', name: 'python_client.py' }
  ]

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800">

      <div className="border-b border-slate-700 bg-slate-900/50 backdrop-blur">
        <div className="max-w-7xl mx-auto px-6 py-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl font-bold text-white mb-2">📓 notebook-to-api</h1>
              <p className="text-slate-400">Transform Jupyter notebooks into production-ready APIs</p>
            </div>
            <div className="text-right">
              <p className="text-sm text-slate-400 mb-2">
                Backend:
                <span className="text-emerald-400 font-semibold">
                  {' '}🟢 Running
                </span>
              </p>
              <a href="http://localhost:8001/docs" target="_blank" rel="noopener noreferrer" className="inline-block bg-emerald-600 hover:bg-emerald-700 text-white text-sm px-3 py-2 rounded transition">
                📖 Open API Docs
              </a>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          <div className="lg:col-span-2 space-y-6">

            <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 hover:border-slate-600 transition">
              <h2 className="text-xl font-semibold text-white mb-4">📤 Upload Notebook</h2>
              <NotebookUpload onSuccess={handleUploadSuccess} />
              {uploadedNotebook && (
                <p className="mt-4 text-sm text-emerald-400">
                  ✅ Uploaded: <code className="bg-slate-900 px-2 py-1 rounded ml-2">{uploadedNotebook}</code>
                </p>
              )}
            </div>

            <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 hover:border-slate-600 transition">
              <h2 className="text-xl font-semibold text-white mb-4">⚙️ Compile</h2>
              <button
                onClick={handleCompile}
                disabled={!uploadedNotebook || isCompiling}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-semibold py-3 px-6 rounded-lg transition"
              >
                {isCompiling ? '🔄 Compiling...' : '🚀 Compile Now'}
              </button>
              {lastCompiled && (
                <p className="text-sm text-emerald-400 mt-3">⏱ Last compiled: {lastCompiled}</p>
              )}
            </div>

            {endpoints.length > 0 && (
              <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
                <h2 className="text-xl font-semibold text-white mb-4">
                  🚀 Generated Endpoints <span className="ml-1 bg-emerald-600 text-white text-sm font-bold px-3 py-1 rounded-full">{endpoints.length}</span>
                </h2>
                <div className="space-y-2">
                  {endpoints.map((endpoint) => (
                    <div key={endpoint} className="flex items-center justify-between bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 hover:border-emerald-500 transition">
                      <div className="flex items-center gap-4">
                        <span className="bg-emerald-600 text-white text-xs font-bold px-2 py-1 rounded">POST</span>
                        <span className="text-emerald-400 font-mono">{endpoint}</span>
                      </div>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(endpoint)
                          setLogs(['📋 Copied ' + endpoint])
                        }}
                        className="bg-slate-700 hover:bg-slate-600 text-white text-sm px-3 py-1 rounded"
                      >
                        Copy
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {functions.length > 0 && (
              <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
                <h2 className="text-xl font-semibold text-white mb-4">
                  🔧 Extracted Functions <span className="ml-1 bg-blue-600 text-white text-sm font-bold px-3 py-1 rounded-full">{functions.length}</span>
                </h2>
                <FunctionList functions={functions} />
              </div>
            )}

            {functions.length > 0 && (
              <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-xl font-semibold text-white">📦 Generated Files</h2>
                  <a href="http://localhost:8000/docs" target="_blank" rel="noopener noreferrer" className="bg-blue-600 hover:bg-blue-700 text-white text-sm px-3 py-2 rounded transition">
                    🌐 Open API
                  </a>
                </div>
                <div className="space-y-2">
                  {generatedFiles.map(({ icon, name }) => (
                    <div key={name} className="bg-slate-900 rounded px-4 py-2 text-slate-300 flex items-center gap-2">
                      <span>{icon}</span>
                      <span>{name}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {endpoints.length > 0 && (
              <EndpointTester endpoints={endpoints} />
            )}
          </div>

          <div className="lg:col-span-1">
            <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 sticky top-6">
              <h2 className="text-xl font-semibold text-white mb-4">📜 Logs</h2>
              <CompilationLogs logs={logs} />
            </div>
          </div>

        </div>
      </div>

    </div>
  )
}