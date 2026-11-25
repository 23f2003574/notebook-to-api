export const WARN_AT = 0.8

export function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return 'unknown'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${unit === 0 ? size : size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`
}

// Uses of the quota: a share of the configured limit (0 limit = unlimited, so no share).
export const quotaShare = (used, limit) => (limit > 0 && Number.isFinite(used) ? used / limit : null)
