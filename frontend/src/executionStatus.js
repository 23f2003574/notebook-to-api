// What an endpoint-tester response says about execution, derived only from what the backend
// returned. The generated app's background tasks report status "processing", "completed",
// "failed" or "cancelled" (POST /{function} answers with the task_id and its status; GET
// /tasks/{id} returns the task). Success is claimed only for a confirmed result.
const TASK_STATES = {
  processing: { icon: '⏳', label: 'Task running (not finished)', tone: 'pending' },
  completed: { icon: '✅', label: 'Task completed', tone: 'success' },
  failed: { icon: '❌', label: 'Task failed', tone: 'failure' },
  cancelled: { icon: '🚫', label: 'Task cancelled', tone: 'neutral' }
}

export function describeExecution({ httpStatus, data, unavailable }) {
  if (unavailable || !httpStatus) {
    return { icon: '⚠️', label: 'Status unavailable', tone: 'unknown' }
  }
  if (httpStatus === 401 || httpStatus === 403) {
    return { icon: '🔒', label: 'Not authorised', tone: 'failure' }
  }
  if (httpStatus >= 500) return { icon: '❌', label: 'Server error', tone: 'failure' }
  if (httpStatus >= 400) return { icon: '❌', label: 'Request failed', tone: 'failure' }
  if (httpStatus < 200 || httpStatus >= 300) return { icon: '⚠️', label: 'Unexpected response', tone: 'unknown' }

  if (data && typeof data === 'object' && typeof data.task_id === 'string') {
    const known = TASK_STATES[data.status]
    return known || { icon: '⚠️', label: `Unknown task state: ${String(data.status)}`, tone: 'unknown' }
  }
  return { icon: '✅', label: 'Succeeded', tone: 'success' }
}

export const TONE_CLASSES = {
  success: 'bg-emerald-600 text-white',
  pending: 'bg-blue-600 text-white',
  failure: 'bg-red-600 text-white',
  neutral: 'bg-slate-600 text-white',
  unknown: 'bg-yellow-600 text-white'
}
