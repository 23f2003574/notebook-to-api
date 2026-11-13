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

describe('EndpointTester API key', () => {
  const setup = (reply) => {
    const fetchMock = vi.fn().mockResolvedValue({ status: reply.status, json: async () => reply.body })
    vi.stubGlobal('fetch', fetchMock)
    const view = render(<EndpointTester endpoints={['/add']} functions={[]} />)
    fireEvent.change(view.container.querySelector('select'), { target: { value: '/add' } })
    return { fetchMock, ...view }
  }
  const keyInput = () => screen.getByLabelText(/API Key/)

  it('accepts typed input in a masked field', () => {
    setup({ status: 200, body: {} })
    fireEvent.change(keyInput(), { target: { value: 'secret-key' } })
    expect(keyInput().value).toBe('secret-key')
    expect(keyInput().type).toBe('password')
  })

  it('sends X-API-Key when a key is entered and keeps the request otherwise unchanged', async () => {
    const { fetchMock } = setup({ status: 200, body: { result: 4 } })
    fireEvent.change(keyInput(), { target: { value: ' secret-key ' } })
    fireEvent.click(screen.getByText('Send Request'))
    expect(await screen.findByText('HTTP 200')).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/add')
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', 'X-API-Key': 'secret-key' })
    expect(JSON.parse(init.body)).toEqual({ example: 'value' })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('omits the header when no key is entered', async () => {
    const { fetchMock } = setup({ status: 200, body: {} })
    fireEvent.click(screen.getByText('Send Request'))
    await screen.findByText('HTTP 200')
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'Content-Type': 'application/json' })
  })

  it('explains a 401 without echoing the key', async () => {
    setup({ status: 401, body: { detail: 'Invalid API key' } })
    fireEvent.click(screen.getByText('Send Request'))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Authentication required.*NOTEBOOK_API_KEY/)

    fireEvent.change(keyInput(), { target: { value: 'wrong-key-123' } })
    fireEvent.click(screen.getByText('Send Request'))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/key was rejected/))
    expect(screen.getByRole('alert').textContent).not.toContain('wrong-key-123')
    expect(document.body.textContent.includes('wrong-key-123')).toBe(false)
  })

  it('never writes the key to browser storage', async () => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    const { fetchMock } = setup({ status: 200, body: {} })
    fireEvent.change(keyInput(), { target: { value: 'secret-key' } })
    fireEvent.click(screen.getByText('Send Request'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
    expect(document.cookie).not.toContain('secret-key')
  })
})

describe('dashboard backend errors', () => {
  const ok = (body) => ({ ok: true, status: 200, json: async () => body })
  const fail = (status, body) => ({ ok: false, status, json: async () => body })
  const upload = (container) =>
    fireEvent.change(container.querySelector('input[type=file]'), {
      target: { files: [new File(['{}'], 'n.ipynb')] },
    })
  const compile = async () => {
    await waitFor(() => expect(screen.getByText(/Compile Now/).closest('button').disabled).toBe(false))
    fireEvent.click(screen.getByText(/Compile Now/))
  }

  it('upload shows the backend detail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fail(400, { detail: 'File must be a .ipynb notebook' })))
    const { container } = render(<Dashboard />)
    upload(container)
    expect(await screen.findByText(/File must be a \.ipynb notebook/)).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/Upload failed(?! \()/)
  })

  it('inspect shows the backend detail and keeps the upload', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(fail(404, { detail: 'Notebook file not found' })))
    const { container } = render(<Dashboard />)
    upload(container)
    expect(await screen.findByText(/Inspection failed: Notebook file not found/)).toBeInTheDocument()
    expect(container.textContent).toContain('n.ipynb')
  })

  it('compile shows the backend detail, and validation errors read clearly', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(ok({ functions: [] }))
      .mockResolvedValueOnce(fail(404, { detail: 'Notebook file not found' }))
      .mockResolvedValueOnce(fail(422, { detail: [{ loc: ['body', 'notebook_path'], msg: 'Field required' }] }))
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<Dashboard />)
    upload(container)
    await compile()
    expect(await screen.findByText(/Compilation failed: Notebook file not found/)).toBeInTheDocument()
    await compile()
    expect(await screen.findByText(/Compilation failed: notebook_path: Field required/)).toBeInTheDocument()
  })

  it('falls back safely for an empty or non-JSON error body', async () => {
    const broken = { ok: false, status: 500, json: async () => { throw new SyntaxError('bad json') } }
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(ok({ functions: [] }))
      .mockResolvedValueOnce(broken))
    const { container } = render(<Dashboard />)
    upload(container)
    await compile()
    expect(await screen.findByText(/Compilation failed: Unknown error \(HTTP 500\)/)).toBeInTheDocument()
  })

  it('successful upload, inspect and compile are unchanged', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(ok({ functions: [{ name: 'add' }] }))
      .mockResolvedValueOnce(ok({ functions: [{ name: 'add' }], dependencies: ['numpy'] })))
    const { container } = render(<Dashboard />)
    upload(container)
    expect(await screen.findByText(/Found 1 functions/)).toBeInTheDocument()
    await compile()
    expect(await screen.findByText(/Compilation successful/)).toBeInTheDocument()
    expect(container.textContent).toContain('Dependencies: numpy')
  })
})
