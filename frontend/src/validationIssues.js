// Turns a validation result (POST /api/validate, or one entry of GET /api/validate-all) into a flat list
// of { severity: 'error' | 'warning', text }. The verdict and the rules come from the backend; this only
// words them, so the page never re-implements a check.
const names = (value) => (Array.isArray(value) ? value : [])
const where = (item) => [Number.isInteger(item?.cell) && `cell ${item.cell}`, Number.isInteger(item?.line) && `line ${item.line}`].filter(Boolean).join(', ')

export function validationIssues(result) {
  if (!result || typeof result !== 'object') return []
  const issues = []
  const add = (severity, text) => issues.push({ severity, text })
  if (result.detail) add('error', String(result.detail))
  names(result.reserved_name_conflicts).forEach((n) => add('error', `Function "${n}" uses a name the generated app reserves; rename it.`))
  if (result.requirements_conflict) add('error', `Conflicting requirements: ${typeof result.requirements_conflict === 'string' ? result.requirements_conflict : JSON.stringify(result.requirements_conflict)}`)
  names(result.duplicate_functions).forEach((n) => add('warning', `Function "${n}" is defined more than once; only the last definition becomes an endpoint.`))
  names(result.skipped_functions).forEach((s) => add('warning', `Function "${s?.name ?? s}" gets no endpoint${s?.reason ? `: ${s.reason}` : '.'}`))
  names(result.import_time_hazards).forEach((h) => add('warning', `${h.call || h.kind || 'Import-time code'} runs when the app starts${where(h) ? ` (${where(h)})` : ''}.`))
  names(result.cells_with_errors).forEach((c) => add('warning', `A cell with a saved error output${typeof c === 'number' ? ` (cell ${c})` : ''}.`))
  names(result.unrecognized_directives).forEach((d) => add('warning', `Unrecognised directive "${d?.directive ?? d}" is ignored.`))
  names(result.ignored_cache_directives).forEach((d) => add('warning', `Cache directive ignored: ${typeof d === 'string' ? d : d?.function ?? JSON.stringify(d)}.`))
  if (result.no_endpoints) add('warning', 'No function in this notebook can become an endpoint.')
  // Anything the backend only reports as a startup warning and that is not covered above.
  const covered = issues.map((i) => i.text).join(' ')
  names(result.startup_warnings).forEach((w) => { if (!covered.includes(String(w))) add('warning', String(w)) })
  return issues
}
