import { useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { validationIssues } from '../validationIssues'

const VERDICT = { pass: ['✅', 'Would compile cleanly', 'text-emerald-400'], warn: ['⚠️', 'Would compile, with warnings', 'text-amber-300'], fail: ['❌', 'Would fail to compile', 'text-red-400'] }

export function IssueList({ result }) {
  const issues = validationIssues(result)
  if (issues.length === 0) return <p className="text-xs text-slate-400 mt-1">No issues found.</p>
  return (
    <ul className="mt-1 space-y-1 text-xs">
      {issues.map((issue, index) => (
        <li key={index} className={issue.severity === 'error' ? 'text-red-300' : 'text-amber-200'}>
          <span className="sr-only">{issue.severity}: </span>{issue.severity === 'error' ? '❌' : '⚠️'} {issue.text}
        </li>
      ))}
    </ul>
  )
}

export function Verdict({ status }) {
  const [icon, text, color] = VERDICT[status] || ['❔', 'Unknown result', 'text-slate-300']
  return <p role="status" className={`text-sm ${color}`}>{icon} {text}</p>
}

// Checks one uploaded notebook with POST /api/validate: nothing is compiled or written.
export default function NotebookValidation({ filename, canValidate = true }) {
  const [state, setState] = useState({ status: 'idle', result: null, error: '' })

  const run = async () => {
    setState({ status: 'loading', result: null, error: '' })
    try {
      const response = await dashboardFetch('/api/validate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notebook_path: filename }),
      })
      if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not validate the notebook'))
      setState({ status: 'ready', result: await response.json(), error: '' })
    } catch (err) {
      setState({ status: 'error', result: null, error: err.message })
    }
  }

  return (
    <section aria-label={`Validate ${filename}`} className="mt-2 text-slate-200">
      <button type="button" className="text-sm text-blue-400 hover:text-blue-300 disabled:opacity-50"
        disabled={!canValidate || state.status === 'loading'}
        title={canValidate ? undefined : "Your key's scope cannot validate notebooks"} onClick={run}>
        {state.status === 'loading' ? 'Validating...' : 'Check before compiling'}
      </button>
      {state.status === 'error' && <p role="alert" className="text-sm text-red-400 mt-1">❌ {state.error}</p>}
      {state.status === 'ready' && (<><Verdict status={state.result.status} /><IssueList result={state.result} /></>)}
    </section>
  )
}
