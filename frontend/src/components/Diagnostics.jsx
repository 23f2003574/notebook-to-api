import { emptyDiagnostics } from '../diagnostics'

export default function Diagnostics({ diagnostics }) {
  const { skipped, inferredBackground, warnings } = diagnostics || emptyDiagnostics
  if (skipped.length === 0 && inferredBackground.length === 0 && warnings.length === 0) return null

  return (
    <div className="space-y-3 text-sm" data-testid="diagnostics">
      {skipped.length > 0 && (
        <div>
          <p className="text-amber-400 font-semibold">⚠ Skipped functions (no endpoint)</p>
          <ul className="mt-1 space-y-1 text-slate-300">
            {skipped.map((f) => (
              <li key={f.name}><code className="text-amber-300">{f.name}</code>: {f.reason}</li>
            ))}
          </ul>
        </div>
      )}
      {inferredBackground.length > 0 && (
        <div>
          <p className="text-blue-300 font-semibold">ⓘ Background endpoints inferred from their names</p>
          <ul className="mt-1 space-y-1 text-slate-300">
            {inferredBackground.map((name) => (
              <li key={name}>
                <code className="text-blue-300">{name}</code> returns a task_id; add{' '}
                <code># notebook-to-api: sync</code> or <code># notebook-to-api: background</code> to make it explicit
              </li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div>
          <p className="text-amber-400 font-semibold">⚠ Startup warnings</p>
          <ul className="mt-1 space-y-1 text-slate-300">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
