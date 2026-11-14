import { useEffect, useRef, useState } from 'react'
import NotebookUpload from './NotebookUpload'
import { appUrl, dashboardApiUrl, dashboardHeaders } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import FunctionList from './FunctionList'
import CompilationLogs from './CompilationLogs'
import EndpointTester from "./EndpointTester"

// Backend lists are used only when well formed; anything else counts as "not returned".
const asList = (value) => (Array.isArray(value) ? value : [])
const asEndpoints = (value) => asList(value).filter((e) => e && typeof e.path === 'string')
const asFiles = (value) => asList(value).filter((f) => typeof f === 'string')

// Presentation only: the file names themselves always come from the backend response.
function fileIcon(name) {
  if (name.endsWith('.py')) return '🐍'
  if (name === 'Dockerfile' || name.startsWith('docker-compose')) return '🐳'
  if (name.endsWith('.md')) return '📖'
  if (name.endsWith('.txt')) return '📋'
  if (/\.(ya?ml)$/.test(name)) return '⚙️'
  return '📄'
}

const HEALTH_POLL_MS = 15000
const HEALTH_TIMEOUT_MS = 5000

// Backend health as reported by GET /api/health: 'checking' until the first answer,
// 'running' only for a successful response whose status is "healthy", else 'unavailable'.
function useBackendHealth() {
  const [status, setStatus] = useState('checking')

  useEffect(() => {
    let active = true
    let latest = 0
    let controller = null

    const check = async () => {
      const id = ++latest
      controller?.abort()
      const current = new AbortController()
      controller = current
      const timer = setTimeout(() => current.abort(), HEALTH_TIMEOUT_MS)
      let next = 'unavailable'
      try {
        const response = await fetch(dashboardApiUrl('/api/health'), {
          headers: dashboardHeaders(),
          signal: current.signal
        })
        if (response.ok) {
          const body = await response.json()
          if (body && body.status === 'healthy') next = 'running'
        }
      } catch {
        // network failure, timeout, abort or unreadable body: unavailable
      } finally {
        clearTimeout(timer)
      }
      if (active && id === latest) setStatus(next)
    }

    check()
    const interval = setInterval(check, HEALTH_POLL_MS)
    return () => {
      active = false
      clearInterval(interval)
      controller?.abort()
    }
  }, [])

  return status
}

const HEALTH_DISPLAY = {
  checking: { label: 'Checking…', icon: '🟡', color: 'text-amber-400' },
  running: { label: 'Running', icon: '🟢', color: 'text-emerald-400' },
  unavailable: { label: 'Unavailable', icon: '🔴', color: 'text-red-400' }
}

export default function Dashboard() {
  const backendStatus = useBackendHealth()
  const health = HEALTH_DISPLAY[backendStatus]
  const [uploadedNotebook, setUploadedNotebook] = useState(null)
  const [functions, setFunctions] = useState([])
  const [logs, setLogs] = useState([])
  const [isCompiling, setIsCompiling] = useState(false)
  const [lastCompiled, setLastCompiled] = useState(null)
  const [endpoints, setEndpoints] = useState([]) // [{ path, method, ... }] as returned by the backend
  const [generatedFiles, setGeneratedFiles] = useState([])

  // Inspect and compile each describe one notebook. Starting new work (a fresh upload, or a
  // compile) supersedes whatever is still in flight: its request is aborted and, should its
  // response still arrive, it is ignored, so an older notebook's functions, endpoints, files
  // and logs can never replace the current ones. Unmounting supersedes everything.
  const workRef = useRef({ id: 0, controller: null })
  const beginWork = () => {
    workRef.current.controller?.abort()
    const controller = new AbortController()
    workRef.current = { id: workRef.current.id + 1, controller }
    return { id: workRef.current.id, signal: controller.signal }
  }
  const isCurrent = (work) => workRef.current.id === work.id
  useEffect(() => () => {
    workRef.current.id += 1
    workRef.current.controller?.abort()
  }, [])

  const handleUploadSuccess = async (response) => {
    const work = beginWork()
    setIsCompiling(false)
    setUploadedNotebook(response.filename)
    setLogs(['✅ Notebook uploaded successfully', '🔍 Inspecting notebook...'])

    try {
      const inspectResponse = await fetch(dashboardApiUrl('/api/inspect'), {
        method: 'POST',
        headers: dashboardHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ notebook_path: response.filename }),
        signal: work.signal
      })

      if (!isCurrent(work)) return
      if (inspectResponse.ok) {
        const inspectData = await inspectResponse.json()
        if (!isCurrent(work)) return
        setFunctions(inspectData.functions || [])
        const returnedEndpoints = asEndpoints(inspectData.endpoints)
        setEndpoints(returnedEndpoints)
        setGeneratedFiles(asFiles(inspectData.generated_files))
        setLogs([
          '✅ Upload successful',
          '📦 Found ' + (inspectData.functions?.length || 0) + ' functions',
          '🚀 Generated ' + returnedEndpoints.length + ' endpoints'
        ])
      } else {
        const detail = await errorFromResponse(inspectResponse, 'Unknown error')
        if (!isCurrent(work)) return
        setLogs(['⚠️ Upload succeeded', '❌ Inspection failed: ' + detail])
      }
    } catch (error) {
      if (!isCurrent(work)) return
      setLogs(['⚠️ Upload succeeded', '❌ Inspection error: ' + error.message])
    }
  }

  const handleCompile = async () => {
    if (!uploadedNotebook) {
      setLogs(['❌ No notebook uploaded'])
      return
    }

    const work = beginWork()
    setIsCompiling(true)
    setLogs(['🔄 Starting compilation...'])

    try {
      const response = await fetch(dashboardApiUrl('/api/compile'), {
        method: 'POST',
        headers: dashboardHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ notebook_path: uploadedNotebook }),
        signal: work.signal
      })

      if (!isCurrent(work)) return
      if (response.ok) {
        const data = await response.json()
        if (!isCurrent(work)) return
        setFunctions(data.functions || [])
        if (Array.isArray(data.endpoints)) setEndpoints(asEndpoints(data.endpoints))
        if (Array.isArray(data.generated_files)) setGeneratedFiles(asFiles(data.generated_files))
        setLogs([
          '✅ Compilation successful',
          '📦 Generated ' + (data.functions?.length || 0) + ' functions',
          '📚 Dependencies: ' + (data.dependencies?.join(', ') || 'none')
        ])
        setLastCompiled(new Date().toLocaleTimeString())
      } else {
        const detail = await errorFromResponse(response, 'Unknown error')
        if (!isCurrent(work)) return
        setLogs(['❌ Compilation failed: ' + detail])
      }
    } catch (error) {
      if (!isCurrent(work)) return
      setLogs(['❌ Error: ' + error.message])
    } finally {
      if (isCurrent(work)) setIsCompiling(false)
    }
  }

  const endpointPaths = endpoints.map(endpoint => endpoint.path)

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
              <p className="text-sm text-slate-400 mb-2" role="status" aria-live="polite">
                Backend:
                <span className={health.color + ' font-semibold'}>
                  {' '}<span aria-hidden="true">{health.icon}</span> {health.label}
                </span>
              </p>
              <a href={dashboardApiUrl('/docs')} target="_blank" rel="noopener noreferrer" className="inline-block bg-emerald-600 hover:bg-emerald-700 text-white text-sm px-3 py-2 rounded transition">
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
                  {endpoints.map(({ path: endpoint, method }) => (
                    <div key={endpoint} className="flex items-center justify-between bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 hover:border-emerald-500 transition">
                      <div className="flex items-center gap-4">
                        <span className="bg-emerald-600 text-white text-xs font-bold px-2 py-1 rounded">{method || 'POST'}</span>
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

            {generatedFiles.length > 0 && (
              <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-xl font-semibold text-white">📦 Generated Files</h2>
                  <a href={appUrl('/docs')} target="_blank" rel="noopener noreferrer" className="bg-blue-600 hover:bg-blue-700 text-white text-sm px-3 py-2 rounded transition">
                    🌐 Open API
                  </a>
                </div>
                <div className="space-y-2">
                  {generatedFiles.map((name) => (
                    <div key={name} className="bg-slate-900 rounded px-4 py-2 text-slate-300 flex items-center gap-2">
                      <span>{fileIcon(name)}</span>
                      <span>{name}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {endpoints.length > 0 && (
              <EndpointTester
                endpoints={endpointPaths}
                functions={functions}
              />
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