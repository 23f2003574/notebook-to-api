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

describe('actual inspection data', () => {
  // Shapes copied from the real POST /api/inspect and /api/compile responses.
  const fn = {
    name: 'double', return_type: 'int', is_async: false,
    args: [
      { name: 'x', type: 'int', has_default: false, kind: 'positional' },
      { name: 'label', type: 'str', has_default: true, default: 'a', kind: 'positional' },
    ],
  }
  const inspected = {
    functions: [fn],
    endpoints: [{ path: '/double', method: 'POST', is_async: false, deprecated: false }],
    generated_files: ['Dockerfile', 'app.py', 'requirements.txt', 'runtime/notebook_module.py'],
  }
  const ok = (body) => ({ ok: true, status: 200, json: async () => body })
  const open = async (inspect) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(ok({ filename: 'n.ipynb' })).mockResolvedValueOnce(ok(inspect)))
    const view = render(<Dashboard />)
    fireEvent.change(view.container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    await screen.findByText(/Found \d+ functions/)
    return view
  }

  it('renders function arguments from args', () => {
    const { container } = render(<FunctionList functions={[fn]} />)
    expect(container.textContent).toContain('Params: x: int, label: str')
  })

  it('says "none" for an empty args list and shows no params line when args is absent', () => {
    const { container, rerender } = render(<FunctionList functions={[{ name: 'f', args: [] }]} />)
    expect(container.textContent).toContain('Params: none')
    rerender(<FunctionList functions={[{ name: 'f' }]} />)
    expect(container.textContent).not.toContain('Params')
  })

  it('lists generated files and endpoints exactly as the backend returned them', async () => {
    const { container } = await open(inspected)
    expect(screen.getByText(/Generated Files/)).toBeInTheDocument()
    for (const name of inspected.generated_files) expect(screen.getByText(name)).toBeInTheDocument()
    expect(container.textContent).toContain('Generated 1 endpoints')
    expect(screen.getAllByText('/double').length).toBeGreaterThan(0)
    expect(screen.getByText('POST')).toBeInTheDocument()
  })

  it('does not show files or endpoints the backend did not return', async () => {
    const { container } = await open(inspected)
    expect(container.textContent).not.toContain('openapi.json')
    expect(container.textContent).not.toContain('python_client.py')
    expect(screen.queryByText('/add')).toBeNull()
  })

  it('survives partial or malformed responses', async () => {
    const { container } = await open({ functions: [{ name: 'f' }], endpoints: [null, 5, { method: 'GET' }, { path: '/ok' }], generated_files: ['app.py', 7, null] })
    expect(container.textContent).toContain('Generated 1 endpoints')
    expect(screen.getByText('app.py')).toBeInTheDocument()
  })

  it('shows no endpoints or file panel when the backend returns none', async () => {
    const { container } = await open({ functions: [{ name: 'f' }] })
    expect(container.textContent).toContain('Generated 0 endpoints')
    expect(screen.queryByText(/Generated Files/)).toBeNull()
  })

  it('compile refreshes files and endpoints from its own response', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(ok(inspected))
      .mockResolvedValueOnce(ok({ functions: [fn], endpoints: [{ path: '/triple', method: 'POST' }], generated_files: ['app.py'] }))
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<Dashboard />)
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    await screen.findByText(/Found 1 functions/)
    fireEvent.click(screen.getByText(/Compile Now/))
    await screen.findByText(/Compilation successful/)
    expect(screen.getAllByText('/triple').length).toBeGreaterThan(0)
    expect(screen.queryByText('Dockerfile')).toBeNull()
  })
})

describe('NotebookUpload drag and drop', () => {
  const notebook = () => new File(['{}'], 'n.ipynb')
  const ok = { ok: true, status: 200, json: async () => ({ filename: 'n.ipynb' }) }
  const setup = () => {
    const fetchMock = vi.fn().mockResolvedValue(ok)
    vi.stubGlobal('fetch', fetchMock)
    const onSuccess = vi.fn()
    const view = render(<NotebookUpload onSuccess={onSuccess} />)
    return { fetchMock, onSuccess, zone: view.container.querySelector('label'), ...view }
  }
  const drop = (zone, files) => fireEvent.drop(zone, { dataTransfer: { files } })

  it('uploads a dropped .ipynb through the normal upload flow and stops the browser opening it', async () => {
    const { fetchMock, onSuccess, zone } = setup()
    const notPrevented = drop(zone, [notebook()])
    expect(notPrevented).toBe(false) // default navigation was cancelled
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith({ filename: 'n.ipynb' }))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/upload$/)
    expect(init.body.get('file').name).toBe('n.ipynb')
  })

  it('rejects an unsupported dropped file without calling the API', async () => {
    const { fetchMock, zone } = setup()
    drop(zone, [new File(['x'], 'notes.txt')])
    expect(await screen.findByText(/Please upload a \.ipynb file/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('treats an empty drop as an error and sends nothing', async () => {
    const { fetchMock, zone } = setup()
    drop(zone, [])
    expect(await screen.findByText(/No file was dropped/)).toBeInTheDocument()
    fireEvent.drop(zone, {})
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('accepts exactly one file: several dropped files upload nothing', async () => {
    const { fetchMock, zone } = setup()
    drop(zone, [notebook(), new File(['{}'], 'b.ipynb')])
    expect(await screen.findByText(/Drop one notebook at a time/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows a drag-over state that clears on leave and on drop', () => {
    const { zone } = setup()
    expect(zone.dataset.dragging).toBe('false')
    fireEvent.dragOver(zone, { dataTransfer: { files: [] } })
    expect(zone.dataset.dragging).toBe('true')
    fireEvent.dragLeave(zone)
    expect(zone.dataset.dragging).toBe('false')
    fireEvent.dragEnter(zone)
    expect(zone.dataset.dragging).toBe('true')
    drop(zone, [])
    expect(zone.dataset.dragging).toBe('false')
  })

  it('still uploads a file chosen with the picker, and the input stays keyboard-focusable', async () => {
    const { fetchMock, onSuccess, container } = setup()
    const input = container.querySelector('input[type=file]')
    expect(input.className).not.toMatch(/\bhidden\b/) // display:none would drop it from the tab order
    fireEvent.change(input, { target: { files: [notebook()] } })
    await waitFor(() => expect(onSuccess).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
