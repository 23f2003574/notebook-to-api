import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import OperationHistory from './OperationHistory'

const reply = (body, status = 200) => ({
  ok: status < 400, status, headers: { get: () => null },
  json: async () => body, text: async () => JSON.stringify(body), clone() { return this },
})
const COMPILES = [
  { compiled_at: '2026-01-01T10:00:00+00:00', notebook_filename: 'old.ipynb', endpoint_count: 1, dependency_count: 2, secret_token: 'LEAK' },
  { compiled_at: '2026-01-02T10:00:00+00:00', notebook_filename: 'new.ipynb', endpoint_count: 3, dependency_count: 1, source_notebook_sha256: 'abcdef0123456789' },
]
const open = () => fireEvent.click(screen.getByRole('button', { name: 'Show compile and deploy history' }))

afterEach(() => vi.unstubAllGlobals())

describe('OperationHistory', () => {
  it('shows a loading state, then newest-first entries without unlisted fields', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ entries: COMPILES })))
    render(<OperationHistory />)
    open()
    expect(screen.getByRole('status')).toHaveTextContent('Loading compile history')
    const list = await screen.findByTestId('history-compile')
    const items = list.querySelectorAll('li')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('new.ipynb')
    expect(items[0]).toHaveTextContent('3 endpoints')
    expect(items[0]).toHaveTextContent('sha256 abcdef012345')
    expect(items[1]).toHaveTextContent('Succeeded')
    expect(document.body.textContent).not.toContain('LEAK')
    expect(screen.getByRole('link', { name: 'open the audit trail' })).toHaveAttribute('href', '#governance-audit')
  })

  it('explains an empty history and switches to deployments', async () => {
    const fetchMock = vi.fn(async (url) => reply(String(url).includes('deploy')
      ? { entries: [{ deployed_at: '2026-01-03T10:00:00+00:00', tag: 'app:1', platform: 'linux/amd64', pushed: false, source_notebook_filename: 'n.ipynb' }] }
      : { entries: [] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<OperationHistory />)
    open()
    expect(await screen.findByText(/No compile history yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Deployments' }))
    expect(await screen.findByTestId('history-deploy')).toHaveTextContent('image app:1 · platform linux/amd64 · built locally')
  })

  it('reports an API failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'history is unavailable' }, 500)))
    render(<OperationHistory />)
    open()
    expect(await screen.findByRole('alert')).toHaveTextContent('history is unavailable')
  })

  it('lets an admin clear history only after confirming', async () => {
    const calls = []
    let entries = COMPILES
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      calls.push(init.method || 'GET')
      if (init.method === 'DELETE') { entries = []; return reply({ deleted_count: 2 }) }
      return reply({ entries })
    }))
    render(<OperationHistory caller={{ scope: 'admin', can: { clear_history: true } }} />)
    open()
    fireEvent.click(await screen.findByRole('button', { name: 'Clear compile history' }))
    expect(calls).not.toContain('DELETE')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(calls).not.toContain('DELETE')
    fireEvent.click(screen.getByRole('button', { name: 'Clear compile history' }))
    fireEvent.click(screen.getByRole('button', { name: 'Yes, clear compile history' }))
    await waitFor(() => expect(screen.getByText(/Cleared 2 compile history entries/)).toBeInTheDocument())
    expect(await screen.findByText(/No compile history yet/)).toBeInTheDocument()
  })

  it('keeps history visible but not clearable for a read-scope key', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ entries: COMPILES })))
    render(<OperationHistory caller={{ scope: 'read', can: { clear_history: false } }} />)
    open()
    expect(await screen.findByTestId('history-compile')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear compile history' })).toBeDisabled()
  })
})
