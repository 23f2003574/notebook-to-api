import { keyExpiryMessage } from '../keyExpiry'

export default function KeyExpiryNotice({ caller }) {
  const message = keyExpiryMessage(caller)
  if (!message) return null
  return (
    <p className="text-sm text-amber-300 mr-3" role="status" data-testid="key-expiry">
      <span aria-hidden="true">⚠</span> {message}
    </p>
  )
}
