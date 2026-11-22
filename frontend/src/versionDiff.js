// "f(a: int, b: int = 1) -> int" from a function entry of the backend's diff.
export function signature(fn) {
  const args = (Array.isArray(fn?.args) ? fn.args : []).map((arg) => {
    const type = arg.type ? `: ${arg.type}` : ''
    const dflt = arg.has_default ? ` = ${arg.default === null || arg.default === undefined ? '...' : JSON.stringify(arg.default)}` : ''
    return `${arg.name}${type}${dflt}`
  })
  return `${fn?.name ?? '?'}(${args.join(', ')})${fn?.return_type ? ` -> ${fn.return_type}` : ''}`
}
