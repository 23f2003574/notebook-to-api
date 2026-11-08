import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CompilationLogs from './CompilationLogs'
import Dashboard from './Dashboard'
import EndpointTester from './EndpointTester'
import FunctionList from './FunctionList'
import NotebookUpload from './NotebookUpload'

afterEach(() => vi.unstubAllGlobals())

describe('CompilationLogs', () => {
  it('shows a placeholder when empty and every line otherwise', () => {
    const { rerender } = render(<CompilationLogs logs={[]} />)
    expect(screen.getByText('Logs appear here...')).toBeInTheDocument()

    rerender(<CompilationLogs logs={['first', 'second']} />)
    expect(screen.getByText('first')).toBeInTheDocument()
    expect(screen.getByText('second')).toBeInTheDocument()
    expect(screen.queryByText('Logs appear here...')).toBeNull()
  })
})

describe('FunctionList', () => {
  it('shows an empty message without functions', () => {
    render(<FunctionList functions={[]} />)
    expect(screen.getByText('No functions extracted yet')).toBeInTheDocument()
  })

  it('shows name, params and return type', () => {
    render(<FunctionList functions={[{ name: 'add', params: ['a', 'b'], return_type: 'int' }]} />)
    expect(screen.getByText('add')).toBeInTheDocument()
    expect(screen.getByText(/Params: a, b/)).toBeInTheDocument()
    expect(screen.getAllByText('int').length).toBeGreaterThan(0)
  })
})

describe('NotebookUpload', () => {
  const upload = (container, file) =>
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [file] } })

  it('rejects a non-notebook file without calling the API', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const onSuccess = vi.fn()
    const { container } = render(<NotebookUpload onSuccess={onSuccess} />)

    upload(container, new File(['x'], 'data.csv'))

    expect(screen.getByText(/Please upload a .ipynb file/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('posts a notebook and reports the response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ filename: 'nb.ipynb' }) })
    vi.stubGlobal('fetch', fetchMock)
    const onSuccess = vi.fn()
    const { container } = render(<NotebookUpload onSuccess={onSuccess} />)

    upload(container, new File(['{}'], 'nb.ipynb'))

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith({ filename: 'nb.ipynb' }))
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/upload$/)
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
  })
})

describe('dashboard API key', () => {
  it('is sent on uploads only when VITE_DASHBOARD_API_KEY is set', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<NotebookUpload onSuccess={() => {}} />)
    const input = () => container.querySelector('input[type=file]')

    fireEvent.change(input(), { target: { files: [new File(['{}'], 'a.ipynb')] } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0][1].headers).toEqual({})

    vi.stubEnv('VITE_DASHBOARD_API_KEY', 'k')
    fireEvent.change(input(), { target: { files: [new File(['{}'], 'b.ipynb')] } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock.mock.calls[1][1].headers).toEqual({ 'X-API-Key': 'k' })
    vi.unstubAllEnvs()
  })
})

describe('EndpointTester', () => {
  it('lists endpoints and rejects an invalid JSON body before sending', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<EndpointTester endpoints={['/add', '/multiply']} functions={[]} />)

    expect(screen.getByRole('option', { name: '/add' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '/multiply' })).toBeInTheDocument()

    fireEvent.change(container.querySelector('select'), { target: { value: '/add' } })
    fireEvent.change(container.querySelector('textarea'), { target: { value: '{not json' } })
    fireEvent.click(screen.getByText('Send Request'))

    expect(await screen.findByText(/Invalid JSON format/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('Dashboard', () => {
  it('keeps Compile disabled until a notebook is uploaded', () => {
    render(<Dashboard />)
    expect(screen.getByText(/Compile Now/).closest('button')).toBeDisabled()
  })
})
