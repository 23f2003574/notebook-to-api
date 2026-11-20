import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import GovernanceAudit from './GovernanceAudit'

afterEach(() => vi.unstubAllGlobals())

const rec = (sequence, action, outcome, metadata = {}) => ({
  sequence, action, outcome, actor: 'dashboard', resource: 'notebook:a.ipynb',
  occurred_at: '2026-11-20T10:00:00+00:00', metadata, previous_hash: 'p', record_hash: 'h',
})

function stub(byAction) {
  const fetchMock = vi.fn((url) => {
    const action = new URL(String(url), 'http://x').searchParams.get('action')
    return Promise.resolve({ ok: true, json: async () => byAction[action] || [] })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('GovernanceAudit', () => {
  it('shows compile and deploy records newest first with notebook, outcome and provenance', async () => {
    const fetchMock = stub({
      'notebook.compile': [rec(1, 'notebook.compile', 'success', { notebook: 'a.ipynb', per_notebook: true })],
      'notebook.deploy': [rec(2, 'notebook.deploy', 'failure', { notebook: 'a.ipynb', error: 'docker missing' })],
    })
    render(<GovernanceAudit />)
    const items = await screen.findAllByTestId(/audit-record-/)
    expect(items.map((i) => i.getAttribute('data-testid'))).toEqual(['audit-record-2', 'audit-record-1'])
    expect(items[0]).toHaveTextContent('Deploy')
    expect(items[0]).toHaveTextContent('✗ failure')
    expect(items[0]).toHaveTextContent('docker missing')
    expect(items[1]).toHaveTextContent('✓ success')
    expect(items[1]).toHaveTextContent('per_notebook')
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toEqual(expect.arrayContaining([
      expect.stringContaining('/governance/audit?action=notebook.compile'),
      expect.stringContaining('/governance/audit?action=notebook.deploy'),
    ]))
  })

  it('shows a loading state, then an empty state', async () => {
    stub({})
    render(<GovernanceAudit />)
    expect(screen.getByText(/Loading audit records/)).toBeInTheDocument()
    expect(await screen.findByText(/No compile or deploy operations/)).toBeInTheDocument()
  })

  it('shows an error and retries', async () => {
    const ok = { ok: true, json: async () => [rec(3, 'notebook.compile', 'success', { notebook: 'b.ipynb' })] }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({ detail: 'store down' }), text: async () => 'store down' })
      .mockResolvedValue(ok)
    vi.stubGlobal('fetch', fetchMock)
    render(<GovernanceAudit />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Retry'))
    await waitFor(() => expect(screen.getAllByTestId(/audit-record-3/).length).toBeGreaterThan(0))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('GovernanceAudit linked record', () => {
  it('opens the record a compile result points to, even when it is not in the recent list', async () => {
    const fetchMock = vi.fn((url) => {
      const path = String(url)
      if (/\/governance\/audit\/7$/.test(path)) return Promise.resolve({ ok: true, status: 200, json: async () => rec(7, 'notebook.compile', 'failure', { notebook: 'z.ipynb', error: 'bad' }) })
      return Promise.resolve({ ok: true, status: 200, json: async () => [] })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<GovernanceAudit focusSequence={7} />)
    const linked = await screen.findByTestId('linked-record')
    expect(linked).toHaveTextContent('#7')
    expect(linked).toHaveTextContent('✗ failure')
    expect(linked).toHaveTextContent('bad')
  })

  it('says so when the linked record no longer exists', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(
      /\/audit\/9$/.test(String(url)) ? { ok: false, status: 404, json: async () => ({}) } : { ok: true, status: 200, json: async () => [] })))
    render(<GovernanceAudit focusSequence={9} />)
    expect(await screen.findByText(/#9 was not found/)).toBeInTheDocument()
  })
})
