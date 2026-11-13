// One place that turns a failed dashboard API response into a user-facing message.
// FastAPI reports errors as {"detail": <string>} or, for request validation (422),
// {"detail": [{"loc": [...], "msg": "..."}, ...]}.
const MAX_LENGTH = 300

function clean(text) {
  // Drop anything from a Python traceback onward, collapse whitespace, cap the length.
  const head = String(text).split('Traceback (most recent call last)')[0]
  const flat = head.replace(/\s+/g, ' ').trim()
  return flat.length > MAX_LENGTH ? flat.slice(0, MAX_LENGTH - 1) + '…' : flat
}

function describeItem(item) {
  if (typeof item === 'string') return clean(item)
  if (!item || typeof item !== 'object') return ''
  const msg = typeof item.msg === 'string' ? clean(item.msg) : ''
  if (!msg) return ''
  const where = Array.isArray(item.loc)
    ? item.loc.filter((part) => part !== 'body' && typeof part !== 'object').join('.')
    : ''
  return where ? `${where}: ${msg}` : msg
}

export function messageFromBody(body, fallback = 'Unknown error') {
  const detail = body && typeof body === 'object' ? body.detail : undefined
  let message = ''
  if (typeof detail === 'string') {
    message = clean(detail)
  } else if (Array.isArray(detail)) {
    message = detail.map(describeItem).filter(Boolean).join('; ')
  } else if (detail && typeof detail === 'object') {
    message = describeItem(detail) || describeItem({ msg: detail.message })
  }
  return message || fallback
}

// Reads the failed response's JSON body (tolerating an empty or non-JSON one).
export async function errorFromResponse(response, fallback = 'Unknown error') {
  let body
  try {
    body = await response.json()
  } catch {
    body = undefined
  }
  const message = messageFromBody(body, '')
  if (message) return message
  return response && response.status ? `${fallback} (HTTP ${response.status})` : fallback
}
