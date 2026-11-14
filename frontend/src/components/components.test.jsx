import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CompilationLogs from './CompilationLogs'
import Dashboard from './Dashboard'
import EndpointTester from './EndpointTester'
import FunctionList from './FunctionList'
import NotebookUpload from './NotebookUpload'

afterEach(() => vi.unstubAllGlobals())

// Dashboard polls GET /api/health on mount; tests that script the other requests' replies in
// order keep their mock for everything except that health probe.
const stubFetch = (mock) =>
  vi.stubGlobal('fetch', (url, init) =>
    /\/api\/health$/.test(String(url))
      ? Promise.resolve({ ok: true, json: async () => ({ status: 'healthy' }) })
      : mock(url, init))

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

describe('NotebookUpload accessibility', () => {
  const notebook = () => new File(['{}'], 'n.ipynb')
  const mount = (reply = { ok: true, json: async () => ({ filename: 'n.ipynb' }) }) => {
    const fetchMock = vi.fn().mockResolvedValue(reply)
    vi.stubGlobal('fetch', fetchMock)
    const view = render(<NotebookUpload onSuccess={vi.fn()} />)
    return { fetchMock, input: view.container.querySelector('input[type=file]'), ...view }
  }

  it('is reachable by keyboard and has an accessible name without the decorative icon', () => {
    const { input } = mount()
    expect(input.tabIndex).toBeGreaterThanOrEqual(0)
    input.focus()
    expect(input).toHaveFocus()
    expect(input).toHaveAccessibleName(/Click to upload or drag & drop/)
    expect(input).not.toHaveAccessibleName(/📁/)
  })

  it('opens the picker once per activation: only the native input handles it', () => {
    const { input, container } = mount()
    const clicks = vi.fn()
    input.addEventListener('click', clicks)
    fireEvent.click(container.querySelector('label'))
    expect(clicks).toHaveBeenCalledTimes(1)
    // No custom key handlers that could duplicate the browser's own Enter/Space activation.
    expect(container.querySelector('label').onkeydown).toBeNull()
    expect(input.onkeydown).toBeNull()
  })

  it('announces a validation error, links it to the input and clears it', async () => {
    const { input, container } = mount()
    fireEvent.change(input, { target: { files: [new File(['x'], 'notes.txt')] } })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Please upload a .ipynb file')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription(/Please upload a \.ipynb file/)
    fireEvent.change(input, { target: { files: [notebook()] } })
    await waitFor(() => expect(container.querySelector('[role=alert]')).toBeNull())
    expect(input).not.toHaveAttribute('aria-invalid')
  })

  it('keeps focus on the control while uploading, ignores input, then announces success', async () => {
    let finish
    const fetchMock = vi.fn(() => new Promise((r) => { finish = r }))
    vi.stubGlobal('fetch', fetchMock)
    const view = render(<NotebookUpload onSuccess={vi.fn()} />)
    const input = view.container.querySelector('input[type=file]')
    input.focus()
    fireEvent.change(input, { target: { files: [notebook()] } })
    expect(await screen.findByRole('status')).toHaveTextContent('Uploading')
    expect(input).toHaveFocus()
    expect(input).not.toBeDisabled()
    expect(input).toHaveAttribute('aria-disabled', 'true')
    expect(fireEvent.click(input)).toBe(false) // picker blocked while busy
    fireEvent.change(input, { target: { files: [notebook()] } })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    finish({ ok: true, json: async () => ({ filename: 'n.ipynb' }) })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Uploaded n.ipynb'))
    expect(input).toHaveFocus()
  })

  it('still accepts a dropped notebook', async () => {
    const { fetchMock, container } = mount()
    fireEvent.drop(container.querySelector('label'), { dataTransfer: { files: [notebook()] } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  })
})

describe('Dashboard request lifecycle', () => {
  const json = (body, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body })
  const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r }); return { promise, resolve } }

  // Routes by URL; /api/inspect and /api/compile replies are controlled per notebook.
  const setup = () => {
    const pending = { inspect: {}, compile: {} }
    const calls = []
    vi.stubGlobal('fetch', vi.fn((url, init = {}) => {
      if (/\/api\/health$/.test(url)) return Promise.resolve(json({ status: 'healthy' }))
      if (/\/api\/upload$/.test(url)) return Promise.resolve(json({ filename: init.body.get('file').name }))
      const kind = /\/api\/inspect$/.test(url) ? 'inspect' : 'compile'
      const name = JSON.parse(init.body).notebook_path
      calls.push({ kind, name, signal: init.signal })
      pending[kind][name] = deferred()
      return pending[kind][name].promise
    }))
    const view = render(<Dashboard />)
    const input = view.container.querySelector('input[type=file]')
    const upload = (name) => fireEvent.change(input, { target: { files: [new File(['{}'], name)] } })
    return { pending, calls, upload, ...view }
  }
  const inspected = (fn) => json({ functions: [{ name: fn }], endpoints: [{ path: '/' + fn, method: 'POST' }], generated_files: [] })

  it('keeps the newest notebook\'s inspection when an older one answers late', async () => {
    const { pending, calls, upload } = setup()
    upload('a.ipynb')
    await waitFor(() => expect(pending.inspect['a.ipynb']).toBeDefined())
    upload('b.ipynb')
    await waitFor(() => expect(pending.inspect['b.ipynb']).toBeDefined())
    expect(calls[0].signal.aborted).toBe(true) // the obsolete request is cancelled
    pending.inspect['b.ipynb'].resolve(inspected('from_b'))
    await waitFor(() => expect(screen.getAllByText('from_b').length).toBeGreaterThan(0))
    pending.inspect['a.ipynb'].resolve(inspected('from_a'))
    await Promise.resolve()
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('from_a')).toBeNull()
    expect(screen.getAllByText('from_b').length).toBeGreaterThan(0)
  })

  it('does not let a late failure of an older inspection replace the current logs', async () => {
    const { pending, upload } = setup()
    upload('a.ipynb')
    await waitFor(() => expect(pending.inspect['a.ipynb']).toBeDefined())
    upload('b.ipynb')
    await waitFor(() => expect(pending.inspect['b.ipynb']).toBeDefined())
    pending.inspect['b.ipynb'].resolve(inspected('from_b'))
    await waitFor(() => expect(screen.getByText(/Found 1 functions/)).toBeInTheDocument())
    pending.inspect['a.ipynb'].resolve(json({ detail: 'old failure' }, false))
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText(/old failure/)).toBeNull()
    expect(screen.getByText(/Found 1 functions/)).toBeInTheDocument()
  })

  it('ignores a compile that finishes after a newer upload and frees the Compile button', async () => {
    const { pending, upload } = setup()
    upload('a.ipynb')
    await waitFor(() => expect(pending.inspect['a.ipynb']).toBeDefined())
    pending.inspect['a.ipynb'].resolve(inspected('from_a'))
    await waitFor(() => expect(screen.getByText(/Compile Now/).closest('button')).not.toBeDisabled())
    fireEvent.click(screen.getByText(/Compile Now/))
    await waitFor(() => expect(pending.compile['a.ipynb']).toBeDefined())
    upload('b.ipynb')
    await waitFor(() => expect(pending.inspect['b.ipynb']).toBeDefined())
    expect(screen.getByText(/Compile Now/).closest('button')).not.toBeDisabled()
    pending.inspect['b.ipynb'].resolve(inspected('from_b'))
    await waitFor(() => expect(screen.getAllByText('from_b').length).toBeGreaterThan(0))
    pending.compile['a.ipynb'].resolve(json({ functions: [{ name: 'compiled_a' }], endpoints: [], generated_files: [], dependencies: [] }))
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText(/Compilation successful/)).toBeNull()
    expect(screen.queryByText('compiled_a')).toBeNull()
    expect(screen.getAllByText('from_b').length).toBeGreaterThan(0)
  })

  it('aborts in-flight requests and makes no state updates after unmount', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { pending, calls, upload, unmount } = setup()
    upload('a.ipynb')
    await waitFor(() => expect(pending.inspect['a.ipynb']).toBeDefined())
    unmount()
    expect(calls[0].signal.aborted).toBe(true)
    pending.inspect['a.ipynb'].resolve(inspected('from_a'))
    await new Promise((r) => setTimeout(r, 0))
    expect(errors).not.toHaveBeenCalled()
    errors.mockRestore()
  })

  it('says when the generated files belong to another notebook instead of listing them', async () => {
    const { pending, upload } = setup()
    upload('b.ipynb')
    await waitFor(() => expect(pending.inspect['b.ipynb']).toBeDefined())
    pending.inspect['b.ipynb'].resolve(json({ functions: [{ name: 'f' }], endpoints: [], generated_files: [], generated_files_status: 'other_notebook' }))
    expect(await screen.findByText(/belong to a different notebook/)).toBeInTheDocument()
  })

  it('does not keep the previous notebook\'s results when the next inspection fails', async () => {
    const { pending, upload } = setup()
    upload('a.ipynb')
    await waitFor(() => expect(pending.inspect['a.ipynb']).toBeDefined())
    pending.inspect['a.ipynb'].resolve(json({ functions: [{ name: 'from_a' }], endpoints: [{ path: '/from_a', method: 'POST' }], generated_files: ['app.py'] }))
    await screen.findByText('app.py')
    upload('b.ipynb')
    await waitFor(() => expect(pending.inspect['b.ipynb']).toBeDefined())
    expect(screen.queryByText('app.py')).toBeNull() // cleared as soon as the new upload starts
    pending.inspect['b.ipynb'].resolve(json({ detail: 'cannot inspect' }, false))
    await screen.findByText(/Inspection failed/)
    expect(screen.queryByText('app.py')).toBeNull()
    expect(screen.queryByText('from_a')).toBeNull()
  })

  it('shows a failed-compilation state instead of the earlier files', async () => {
    const { pending, upload } = setup()
    upload('a.ipynb')
    await waitFor(() => expect(pending.inspect['a.ipynb']).toBeDefined())
    pending.inspect['a.ipynb'].resolve(json({ functions: [{ name: 'f' }], endpoints: [], generated_files: ['old_app.py'] }))
    await screen.findByText('old_app.py')
    fireEvent.click(screen.getByText(/Compile Now/))
    await waitFor(() => expect(pending.compile['a.ipynb']).toBeDefined())
    pending.compile['a.ipynb'].resolve(json({ detail: 'syntax error' }, false))
    expect(await screen.findByText(/Compilation failed, so there are no generated files/)).toBeInTheDocument()
    expect(screen.queryByText('old_app.py')).toBeNull()
  })

  it('still reports the current inspection error', async () => {
    const { pending, upload } = setup()
    upload('a.ipynb')
    await waitFor(() => expect(pending.inspect['a.ipynb']).toBeDefined())
    pending.inspect['a.ipynb'].resolve(json({ detail: 'bad notebook' }, false))
    expect(await screen.findByText(/Inspection failed: .*bad notebook/)).toBeInTheDocument()
  })
})

describe('Dashboard backend health', () => {
  const healthy = { ok: true, json: async () => ({ status: 'healthy' }) }
  afterEach(() => vi.useRealTimers())
  const status = () => screen.getAllByRole('status').find((el) => /Backend/.test(el.textContent))

  it('shows Checking first, then Running after a healthy response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(healthy)
    vi.stubGlobal('fetch', fetchMock)
    render(<Dashboard />)
    expect(status()).toHaveTextContent('Checking')
    await waitFor(() => expect(status()).toHaveTextContent('Running'))
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/health$/)
  })

  it('shows Unavailable when the request is rejected', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')))
    render(<Dashboard />)
    await waitFor(() => expect(status()).toHaveTextContent('Unavailable'))
  })

  it.each([
    ['an HTTP error', { ok: false, json: async () => ({ status: 'healthy' }) }],
    ['an unhealthy body', { ok: true, json: async () => ({ status: 'degraded' }) }]
  ])('does not show Running for %s', async (_, reply) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply))
    render(<Dashboard />)
    await waitFor(() => expect(status()).toHaveTextContent('Unavailable'))
    expect(status()).not.toHaveTextContent('Running')
  })

  it('refreshes the status by polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = vi.fn().mockResolvedValue(healthy)
    vi.stubGlobal('fetch', fetchMock)
    render(<Dashboard />)
    await waitFor(() => expect(status()).toHaveTextContent('Running'))
    fetchMock.mockRejectedValue(new Error('down'))
    await vi.advanceTimersByTimeAsync(15000)
    await waitFor(() => expect(status()).toHaveTextContent('Unavailable'))
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('stops polling and aborts the request on unmount', async () => {
    vi.useFakeTimers()
    let signal
    const fetchMock = vi.fn((url, init) => { signal = init.signal; return new Promise(() => {}) })
    vi.stubGlobal('fetch', fetchMock)
    const { unmount } = render(<Dashboard />)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    unmount()
    expect(signal.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('ignores a stale response that resolves after a newer one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let resolveFirst
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r }))
      .mockResolvedValue({ ok: false, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    render(<Dashboard />)
    await vi.advanceTimersByTimeAsync(15000)
    await waitFor(() => expect(status()).toHaveTextContent('Unavailable'))
    resolveFirst(healthy)
    await vi.advanceTimersByTimeAsync(10)
    expect(status()).toHaveTextContent('Unavailable')
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
    stubFetch(vi.fn().mockResolvedValue(fail(400, { detail: 'File must be a .ipynb notebook' })))
    const { container } = render(<Dashboard />)
    upload(container)
    expect(await screen.findByText(/File must be a \.ipynb notebook/)).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/Upload failed(?! \()/)
  })

  it('inspect shows the backend detail and keeps the upload', async () => {
    stubFetch(vi.fn()
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
    stubFetch(fetchMock)
    const { container } = render(<Dashboard />)
    upload(container)
    await compile()
    expect(await screen.findByText(/Compilation failed: Notebook file not found/)).toBeInTheDocument()
    await compile()
    expect(await screen.findByText(/Compilation failed: notebook_path: Field required/)).toBeInTheDocument()
  })

  it('falls back safely for an empty or non-JSON error body', async () => {
    const broken = { ok: false, status: 500, json: async () => { throw new SyntaxError('bad json') } }
    stubFetch(vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(ok({ functions: [] }))
      .mockResolvedValueOnce(broken))
    const { container } = render(<Dashboard />)
    upload(container)
    await compile()
    expect(await screen.findByText(/Compilation failed: Unknown error \(HTTP 500\)/)).toBeInTheDocument()
  })

  it('successful upload, inspect and compile are unchanged', async () => {
    stubFetch(vi.fn()
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
    stubFetch(vi.fn().mockResolvedValueOnce(ok({ filename: 'n.ipynb' })).mockResolvedValueOnce(ok(inspect)))
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

  it('shows no endpoints and an explicit empty file state when the backend returns none', async () => {
    const { container } = await open({ functions: [{ name: 'f' }] })
    expect(container.textContent).toContain('Generated 0 endpoints')
    expect(screen.getByText(/No files generated yet/)).toBeInTheDocument()
    expect(screen.queryByText('app.py')).toBeNull()
  })

  it('compile refreshes files and endpoints from its own response', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(ok({ filename: 'n.ipynb' }))
      .mockResolvedValueOnce(ok(inspected))
      .mockResolvedValueOnce(ok({ functions: [fn], endpoints: [{ path: '/triple', method: 'POST' }], generated_files: ['app.py'] }))
    stubFetch(fetchMock)
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
