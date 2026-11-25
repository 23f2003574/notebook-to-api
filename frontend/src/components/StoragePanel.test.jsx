import { afterEach, describe, expect, it, vi } from 'vitest'
import axe from 'axe-core'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import StoragePanel from './StoragePanel'
import { formatBytes, quotaShare } from '../storageFormat'
import { errorFromResponse } from '../errorMessage'

const MB = 1024 * 1024
const usage = (over = {}) => ({
  status: 'success', notebook_count: 3, max_notebooks: 10, total_bytes: 10 * MB, max_total_storage_bytes: 100 * MB,
  total_notebook_bytes: 6 * MB, total_version_bytes: 4 * MB, total_version_count: 7,
  free_disk_bytes: 5000 * MB, min_free_disk_bytes: 500 * MB, ...over,
})
const reply = (body, ok = true, status = 200) => ({ ok, status, json: async () => body, headers: { get: () => null } })
afterEach(() => vi.unstubAllGlobals())
function renderOpen(refreshKey) {
  const view = render(<StoragePanel refreshKey={refreshKey} />)
  fireEvent.click(screen.getByRole('button', { name: 'Show storage usage' }))
  return view
}

describe('StoragePanel', () => {
  it('shows usage against the quota, the version-history split and the free disk', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(usage())))
    renderOpen('a')
    expect(await screen.findByTestId('storage-usage')).toBeTruthy()
    expect(screen.getByText('10 MB of 100 MB')).toBeTruthy()
    expect(screen.getByText('3 of 10')).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: 'Storage' }).getAttribute('aria-valuenow')).toBe('10')
    expect(screen.getByText(/Free disk: 4\.9 GB/)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('warns once usage reaches 80% of the quota', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(usage({ total_bytes: 85 * MB }))))
    renderOpen('a')
    expect((await screen.findByRole('alert')).textContent).toMatch(/Storage is at 85% of its limit/)
  })

  it('warns when free disk is close to the reserve', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(usage({ free_disk_bytes: 520 * MB }))))
    renderOpen('a')
    expect((await screen.findByRole('alert')).textContent).toMatch(/close to the reserve/)
  })

  it('says unlimited when no limit is configured, and handles an empty library', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(usage({ max_total_storage_bytes: 0, max_notebooks: 0 }))))
    const { unmount } = renderOpen('a')
    expect((await screen.findAllByText(/no limit set/)).length).toBe(2)
    expect(screen.queryByRole('progressbar')).toBeNull()
    unmount()
    vi.stubGlobal('fetch', vi.fn(async () => reply(usage({ notebook_count: 0, total_bytes: 0 }))))
    renderOpen('b')
    expect(await screen.findByText('Nothing stored yet.')).toBeTruthy()
  })

  it('shows a readable error and retries', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply({ detail: 'boom' }, false, 500))
      .mockResolvedValueOnce(reply(usage()))
    vi.stubGlobal('fetch', fetchMock)
    renderOpen('a')
    expect((await screen.findByRole('alert')).textContent).toMatch(/boom/)
    fireEvent.click(screen.getByText('Retry'))
    await waitFor(() => expect(screen.getByTestId('storage-usage')).toBeTruthy())
  })

  it('has no accessibility violations in the warning state', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(usage({ total_bytes: 90 * MB }))))
    const { container } = renderOpen('a')
    await screen.findByTestId('storage-usage')
    const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
    expect(result.violations.map((v) => v.id)).toEqual([])
  })
})

describe('lazy loading', () => {
  it('does not call the backend until it is opened', () => {
    const fetchMock = vi.fn(async () => reply(usage()))
    vi.stubGlobal('fetch', fetchMock)
    render(<StoragePanel refreshKey="a" />)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('helpers', () => {
  it('formats bytes and computes shares', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(undefined)).toBe('unknown')
    expect(quotaShare(50, 100)).toBe(0.5)
    expect(quotaShare(50, 0)).toBeNull()
  })

  it('explains a quota-rejected upload (HTTP 507) without paths', async () => {
    const response = reply({ detail: 'Free disk space is below the reserve of 500 bytes' }, false, 507)
    expect(await errorFromResponse(response, 'Upload failed')).toBe('Not enough storage: Free disk space is below the reserve of 500 bytes')
  })
})
