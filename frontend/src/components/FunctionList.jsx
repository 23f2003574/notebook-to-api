export default function FunctionList({ functions }) {
  if (!functions || functions.length === 0) {
    return <p className="text-slate-400 text-sm">No functions extracted yet</p>
  }

  return (
    <div className="space-y-3">
      {functions.map((func, idx) => (
        <div key={idx} className="bg-slate-700 p-4 rounded border border-slate-600">
          <div className="flex items-start justify-between mb-2">
            <code className="text-emerald-400 font-mono text-sm">{func.name}</code>
            <div className="flex items-center gap-2">
              <span className="text-xs bg-blue-600/30 text-blue-300 px-2 py-1 rounded">
                {func.type || 'function'}
              </span>

              {func.return_type && (
                <span className="text-xs bg-emerald-600/30 text-emerald-300 px-2 py-1 rounded">
                  {func.return_type}
                </span>
              )}
            </div>
          </div>
          {func.params && (
            <p className="text-xs text-slate-400">
              📥 Params: {Array.isArray(func.params) ? func.params.join(', ') : JSON.stringify(func.params)}
            </p>
          )}
          {func.return_type && (
            <div className="mt-2 flex items-center gap-2">
              <span className="text-xs text-slate-400">
                📤 Returns:
              </span>

              <span className="text-xs bg-emerald-600/30 text-emerald-300 px-2 py-1 rounded">
                {func.return_type}
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}