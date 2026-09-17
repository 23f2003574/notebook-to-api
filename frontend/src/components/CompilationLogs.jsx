export default function CompilationLogs({ logs }) {
  return (
    <div className="bg-slate-900 rounded p-4 font-mono text-xs max-h-96 overflow-y-auto">
      {logs.length === 0 ? (
        <p className="text-slate-500">Logs appear here...</p>
      ) : (
        logs.map((log, idx) => (
          <div key={idx} className="text-slate-300 mb-2 break-words">
            {log}
          </div>
        ))
      )}
    </div>
  )
}
