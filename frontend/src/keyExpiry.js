export const EXPIRY_WARNING_DAYS = 14

// The sentence to show for a key that expires within EXPIRY_WARNING_DAYS, or null when there is
// nothing to warn about (no expiry, a far-off one, or an answer the page cannot trust).
export function keyExpiryMessage(caller) {
  if (!caller || !caller.authenticated) return null
  const days = caller.expires_in_days
  if (!Number.isInteger(days) || days > EXPIRY_WARNING_DAYS || days < 0) return null
  const when = days === 0 ? 'today' : days === 1 ? 'in 1 day' : `in ${days} days`
  return `This dashboard key expires ${when}${caller.expires ? ` (${caller.expires})` : ''}. Ask an administrator for a replacement before then.`
}
