import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import GovernanceAudit from './GovernanceAudit'

afterEach(() => vi.unstubAllGlobals())

const rec = (sequence, action, outcome, metadata = {}) => ({
  sequence, action, outcome, actor: 'dashboard', resource: 'notebook:a.ipynb',
  occurred_at: '2026-11-20T10:00:00+00:00', metadata, previous_hash: 'p', record_hash: 'h',
})
const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) })
const page = (records, next = null) => json({ records, has_more: Boolean(next), next_cursor: next })

function stub(handler) {
  const fetchMock = vi.fn((url) => Promise.resolve(handler(new URL(String(url), 'http://x'))))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('GovernanceAudit records', () => {
  it('shows compile and deploy records with notebook, outcome and provenance, one server query', async () => {
    const fetchMock = stub(() => page([
      rec(2, 'notebook.deploy', 'failure', { notebook: 'a.ipynb', error: 'docker missing' }),
      rec(1, 'notebook.compile', 'success', { notebook: 'a.ipynb', per_notebook: true }),
    ]))
    render(<GovernanceAudit />)
    const items = await screen.findAllByTestId(/audit-record-/)
    expect(items.map((i) => i.getAttribute('data-testid'))).toEqual(['audit-record-2', 'audit-record-1'])
    expect(items[0]).toHaveTextContent('Deploy')
    expect(items[0]).toHaveTextContent('✗ failure')
    expect(items[0]).toHaveTextContent('docker missing')
    expect(items[1]).toHaveTextContent('✓ success')
    expect(items[1]).toHaveTextContent('per_notebook')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/governance/audit/history?scope=notebook&limit=25')
  })

  it('shows a loading state, then an empty state', async () => {
    stub(() => page([]))
    render(<GovernanceAudit />)
    expect(screen.getByText(/Loading audit records/)).toBeInTheDocument()
    expect(await screen.findByText(/No notebook operation records match/)).toBeInTheDocument()
  })

  it('shows an error and retries', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ detail: 'store down' }, 500))
      .mockResolvedValue(page([rec(3, 'notebook.compile', 'success', { notebook: 'b.ipynb' })]))
    vi.stubGlobal('fetch', fetchMock)
    render(<GovernanceAudit />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Retry'))
    await waitFor(() => expect(screen.getAllByTestId(/audit-record-3/).length).toBeGreaterThan(0))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('GovernanceAudit navigation', () => {
  it('sends composed filters to the server', async () => {
    const fetchMock = stub(() => page([]))
    render(<GovernanceAudit />)
    await screen.findByText(/No notebook operation records match/)
    fireEvent.change(screen.getByLabelText('Notebook'), { target: { value: ' a.ipynb ' } })
    fireEvent.change(screen.getByLabelText('Operation'), { target: { value: 'notebook.deploy' } })
    fireEvent.change(screen.getByLabelText('Outcome'), { target: { value: 'failure' } })
    fireEvent.click(screen.getByText('Apply filters'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    const url = new URL(String(fetchMock.mock.calls[1][0]), 'http://x')
    expect(Object.fromEntries(url.searchParams)).toMatchObject({ notebook: 'a.ipynb', action: 'notebook.deploy', outcome: 'failure', scope: 'notebook' })
  })

  it('loads older records with the cursor and appends them', async () => {
    const fetchMock = stub((url) => url.searchParams.get('cursor') === 'c1'
      ? page([rec(1, 'notebook.compile', 'success', { notebook: 'a.ipynb' })])
      : page([rec(2, 'notebook.compile', 'success', { notebook: 'a.ipynb' })], 'c1'))
    render(<GovernanceAudit />)
    fireEvent.click(await screen.findByText('Load older records'))
    await screen.findByTestId('audit-record-1')
    expect(screen.getAllByTestId(/audit-record-/).map((i) => i.getAttribute('data-testid'))).toEqual(['audit-record-2', 'audit-record-1'])
    expect(screen.queryByText('Load older records')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('GovernanceAudit linked record', () => {
  it('opens the record a compile result points to, even when it is not in the loaded page', async () => {
    stub((url) => /\/governance\/audit\/7$/.test(url.pathname)
      ? json(rec(7, 'notebook.compile', 'failure', { notebook: 'z.ipynb', error: 'bad' }))
      : page([]))
    render(<GovernanceAudit focusSequence={7} />)
    const linked = await screen.findByTestId('linked-record')
    expect(linked).toHaveTextContent('#7')
    expect(linked).toHaveTextContent('✗ failure')
    expect(linked).toHaveTextContent('bad')
  })

  it('says so when the linked record no longer exists', async () => {
    stub((url) => /\/audit\/9$/.test(url.pathname) ? json({}, 404) : page([]))
    render(<GovernanceAudit focusSequence={9} />)
    expect(await screen.findByText(/#9 was not found/)).toBeInTheDocument()
  })
})

describe('GovernanceAudit verification', () => {
  const run = async (verifyReply) => {
    stub((url) => /\/audit\/verify$/.test(url.pathname) ? verifyReply : page([]))
    render(<GovernanceAudit />)
    await screen.findByText(/No notebook operation records match/)
    fireEvent.click(screen.getByText('Verify audit chain'))
    return screen.findByTestId('verification')
  }

  it('reports an intact chain', async () => {
    expect(await run(json({ valid: true, checked: 12, first_broken_sequence: null, reason: null }))).toHaveTextContent('Chain intact: 12 records')
  })
  it('distinguishes an empty valid chain', async () => {
    expect(await run(json({ valid: true, checked: 0, first_broken_sequence: null, reason: null }))).toHaveTextContent('valid but empty')
  })
  it('explains a broken chain with where and what to do', async () => {
    const el = await run(json({ valid: false, checked: 5, first_broken_sequence: 3, reason: "record 3's hash does not match its recorded fields" }))
    expect(el).toHaveTextContent('broken at record #3')
    expect(el).toHaveTextContent('cannot be trusted')
  })
  it('says an unavailable chain is not the same as a broken one', async () => {
    const el = await run(json({ detail: 'audit chain unavailable: read failed' }, 503))
    expect(el).toHaveTextContent('Could not verify')
    expect(el).toHaveTextContent('does not mean the chain is broken')
  })
})

describe('GovernanceAudit recovery guide', () => {
  it('loads the server-provided recovery guidance only when opened', async () => {
    const fetchMock = stub((url) => /\/audit\/recovery$/.test(url.pathname)
      ? json({ guarantees: ['One record per request'], if_unavailable: ['Fix the volume'], if_broken: ['Restore a backup'] })
      : page([]))
    render(<GovernanceAudit />)
    await screen.findByText(/No notebook operation records match/)
    expect(fetchMock).toHaveBeenCalledTimes(1) // only the history page so far
    const details = screen.getByTestId('recovery-guide')
    details.open = true
    fireEvent(details, new Event('toggle'))
    expect(await screen.findByText('Restore a backup')).toBeInTheDocument()
    expect(screen.getByText('One record per request')).toBeInTheDocument()
    expect(screen.getByText('Fix the volume')).toBeInTheDocument()
  })
})
