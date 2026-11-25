import { useEffect, useState } from 'react'
import { dashboardFetch } from '../dashboardApi'
import { errorFromResponse } from '../errorMessage'
import { WARN_AT, formatBytes, quotaShare } from '../storageFormat'

function Meter({ label, used, limit, unit }) {
  const share = quotaShare(used, limit)
  const warn = share !== null && share >= WARN_AT
  return (
    <div className="mb-3">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className={warn ? 'text-amber-300' : 'text-slate-300'}>
          {unit === 'bytes' ? formatBytes(used) : used}
          {limit > 0 ? ` of ${unit === 'bytes' ? formatBytes(limit) : limit}` : ' (no limit set)'}
        </span>
      </div>
      {share !== null && (
        <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={Math.min(100, Math.round(share * 100))} className="mt-1 h-2 bg-slate-700 rounded">
          <div className={'h-2 rounded ' + (warn ? 'bg-amber-400' : 'bg-emerald-500')} style={{ width: `${Math.min(100, share * 100)}%` }} />
        </div>
      )}
      {warn && <p role="alert" className="text-xs text-amber-300 mt-1">⚠️ {label} is at {Math.round(share * 100)}% of its limit: delete notebooks or old versions before uploads start failing.</p>}
    </div>
  )
}

// Storage use against the dashboard's quotas (GET /api/notebooks/storage): bytes and notebook count
// against their limits, and the free disk space against the configured reserve.
export default function StoragePanel({ refreshKey }) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState({ key: null, status: 'loading', data: null, error: '' })
  const [reloads, setReloads] = useState(0)
  const key = `${refreshKey}|${reloads}`
  const loading = open && state.key !== key

  useEffect(() => {
    if (!open) return undefined
    const controller = new AbortController()
    ;(async () => {
      try {
        const response = await dashboardFetch('/api/notebooks/storage?limit=1', { signal: controller.signal })
        if (!response.ok) throw new Error(await errorFromResponse(response, 'Could not load storage usage'))
        const data = await response.json()
        if (!controller.signal.aborted) setState({ key, status: 'ready', data, error: '' })
      } catch (err) {
        if (!controller.signal.aborted) setState({ key, status: 'error', data: null, error: err.message })
      }
    })()
    return () => controller.abort()
  }, [open, key])

  const data = state.data
  const free = data && Number.isFinite(data.free_disk_bytes) ? data.free_disk_bytes : null
  const reserve = data?.min_free_disk_bytes > 0 ? data.min_free_disk_bytes : 0
  const lowDisk = free !== null && reserve > 0 && free < reserve * 1.25

  return (
    <section aria-label="Storage usage" className="text-slate-200">
      <button type="button" aria-expanded={open} className="text-sm text-blue-400 hover:text-blue-300 mb-2" onClick={() => setOpen(!open)}>
        {open ? 'Hide storage usage' : 'Show storage usage'}
      </button>
      {loading && <p role="status" className="text-sm text-slate-400">Loading storage usage...</p>}
      {open && !loading && state.status === 'error' && (
        <p role="alert" className="text-sm text-red-400">❌ {state.error}{' '}
          <button type="button" className="underline" onClick={() => setReloads((n) => n + 1)}>Retry</button></p>
      )}
      {open && !loading && state.status === 'ready' && (
        data.notebook_count === 0 && data.total_bytes === 0 ? (
          <p className="text-sm text-slate-400">Nothing stored yet.</p>
        ) : (
          <div data-testid="storage-usage">
            <Meter label="Storage" used={data.total_bytes} limit={data.max_total_storage_bytes} unit="bytes" />
            <Meter label="Notebooks" used={data.notebook_count} limit={data.max_notebooks} unit="count" />
            <p className="text-xs text-slate-400">
              Notebooks {formatBytes(data.total_notebook_bytes)} · version history {formatBytes(data.total_version_bytes)} ({data.total_version_count} versions)
            </p>
            <p className={'text-sm mt-2 ' + (lowDisk ? 'text-amber-300' : 'text-slate-300')}>
              Free disk: {free === null ? 'unknown' : formatBytes(free)}
              {reserve > 0 ? ` (uploads stop below ${formatBytes(reserve)})` : ''}
            </p>
            {lowDisk && <p role="alert" className="text-xs text-amber-300">⚠️ Free disk space is close to the reserve; uploads will be refused below it.</p>}
          </div>
        )
      )}
    </section>
  )
}
