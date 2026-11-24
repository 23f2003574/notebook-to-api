import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import CompilationLogs from './CompilationLogs'
import Diagnostics from './Diagnostics'
import { diagnosticsFrom } from '../diagnostics'
import Dashboard from './Dashboard'
import EndpointTester from './EndpointTester'
import FunctionList from './FunctionList'
import NotebookLibrary from './NotebookLibrary'
import { allows, fetchCaller } from '../dashboardApi'
import NotebookUpload from './NotebookUpload'
import { dashboardFetch, dashboardHeaders, getDashboardKey, setDashboardKey } from '../dashboardApi'

afterEach(() => {
  vi.unstubAllGlobals()
  setDashboardKey('')
})

// Dashboard polls GET /api/health on mount; tests that script the other requests' replies in
// order keep their mock for everything except that health probe.
const stubFetch = (mock) =>
  vi.stubGlobal('fetch', (url, init) =>
    /\/api\/health$/.test(String(url))
      ? Promise.resolve({ ok: true, json: async () => ({ status: 'healthy' }) })
      : /\/api\/whoami$/.test(String(url))
        ? Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
        : /\/governance\/audit/.test(String(url))
        ? Promise.resolve({ ok: true, json: async () => ({ records: [], has_more: false, next_cursor: null }) })
        : /\/api\/notebooks$/.test(String(url))
        ? Promise.resolve({ ok: true, json: async () => ({ notebooks: [] }) })
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

describe('NotebookUpload replacing an existing notebook', () => {
  const upload = (container, file) =>
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [file] } })
  const json = (status, body, headers = {}) => ({
    ok: status < 400, status, headers: { get: (k) => headers[k] ?? null },
    json: async () => body, text: async () => JSON.stringify(body), clone() { return this },
  })
  const conflictFetch = (replaceResponse) => vi.fn(async (url) => {
    const u = String(url)
    if (u.includes('overwrite=true')) return replaceResponse
    if (u.endsWith('/api/upload')) return json(409, { detail: "A notebook named 'nb.ipynb' already exists" })
    if (u.endsWith('/versions')) return json(200, { versions: [{ version_id: 'v-1' }] })
    return json(200, {}, { 'X-Content-SHA256': 'abc123' })
  })

  it('asks first, and cancelling changes nothing', async () => {
    const fetchMock = conflictFetch(json(200, { filename: 'nb.ipynb' }))
    vi.stubGlobal('fetch', fetchMock)
    const onSuccess = vi.fn()
    const { container } = render(<NotebookUpload onSuccess={onSuccess} />)
    upload(container, new File(['{}'], 'nb.ipynb'))
    expect(await screen.findByText('Replace existing notebook?')).toBeInTheDocument()
    expect(screen.getByText('nb.ipynb')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Replace existing notebook?')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('overwrite=true'))).toBe(false)
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('confirming replaces with overwrite and the current hash, and names the saved version', async () => {
    const fetchMock = conflictFetch(json(200, { filename: 'nb.ipynb', overwritten: true }))
    vi.stubGlobal('fetch', fetchMock)
    const onSuccess = vi.fn()
    const { container } = render(<NotebookUpload onSuccess={onSuccess} />)
    upload(container, new File(['{}'], 'nb.ipynb'))
    fireEvent.click(await screen.findByRole('button', { name: 'Replace' }))
    await waitFor(() => expect(onSuccess).toHaveBeenCalled())
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('overwrite=true'))
    expect(String(call[0])).toMatch(/expected_sha256=[0-9a-f]{64}/)
    expect(call[1].method).toBe('POST')
    expect(await screen.findByTestId('replaced-notice')).toHaveTextContent('version v-1')
  })

  it('reports a notebook that changed meanwhile instead of replacing it', async () => {
    let reads = 0
    const fetchMock = vi.fn(async (url) => {
      const u = String(url)
      if (u.endsWith('/api/upload')) return json(409, { detail: 'exists' })
      if (u.includes('overwrite=true')) return json(200, {})
      reads += 1
      return json(200, {}, { 'X-Content-SHA256': reads === 1 ? 'abc123' : 'def456' })
    })
    vi.stubGlobal('fetch', fetchMock)
    const onSuccess = vi.fn()
    const { container } = render(<NotebookUpload onSuccess={onSuccess} />)
    upload(container, new File(['{}'], 'nb.ipynb'))
    fireEvent.click(await screen.findByRole('button', { name: 'Replace' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('changed after you opened this dialog')
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('overwrite=true'))).toBe(false)
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('disables replacing for a key without admin scope', async () => {
    vi.stubGlobal('fetch', conflictFetch(json(200, {})))
    const caller = { name: 'ci', scope: 'compile', can: { overwrite: false } }
    const { container } = render(<NotebookUpload onSuccess={vi.fn()} caller={caller} />)
    upload(container, new File(['{}'], 'nb.ipynb'))
    expect(await screen.findByRole('button', { name: 'Replace' })).toBeDisabled()
    expect(screen.getByText(/compile scope cannot replace/)).toBeInTheDocument()
  })
})

describe('NotebookUpload archives', () => {
  it('imports a zip through the import endpoint and reports kept and skipped data files', async () => {
    const reply = { results: [{
      filename: 'm.ipynb', status: 'success',
      companion_files: [{ path: 'weights.json', bytes: 8 }],
      companion_skipped: [{ path: 'link.json', reason: 'symbolic links are not imported' }],
    }] }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => reply })
    vi.stubGlobal('fetch', fetchMock)
    const onSuccess = vi.fn()
    const { container } = render(<NotebookUpload onSuccess={onSuccess} />)
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['PK'], 'bundle.zip')] } })
    const summary = await screen.findByTestId('import-summary')
    expect(summary).toHaveTextContent('m.ipynb')
    expect(summary).toHaveTextContent('Data files kept: weights.json')
    expect(summary).toHaveTextContent('Skipped link.json: symbolic links are not imported')
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/notebooks\/import$/)
    expect(onSuccess).toHaveBeenCalledWith({ filename: 'm.ipynb' })
  })

  it('shows an error when nothing in the archive could be imported', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ results: [{ filename: 'x.ipynb', status: 'error', error: 'already exists' }] }) }))
    const onSuccess = vi.fn()
    const { container } = render(<NotebookUpload onSuccess={onSuccess} />)
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['PK'], 'b.zip')] } })
    expect(await screen.findByRole('alert')).toHaveTextContent('No notebook was imported')
    expect(screen.getByTestId('import-summary')).toHaveTextContent('already exists')
    expect(onSuccess).not.toHaveBeenCalled()
  })
})

describe('dashboard API key', () => {
  it('is sent on uploads only once the user has entered it', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<NotebookUpload onSuccess={() => {}} />)
    const input = () => container.querySelector('input[type=file]')

    fireEvent.change(input(), { target: { files: [new File(['{}'], 'a.ipynb')] } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0][1].headers).toEqual({})

    setDashboardKey('k')
    fireEvent.change(input(), { target: { files: [new File(['{}'], 'b.ipynb')] } })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock.mock.calls[1][1].headers).toEqual({ 'X-API-Key': 'k' })
  })

  it('ignores a build-time VITE_DASHBOARD_API_KEY, which would ship in the public bundle', async () => {
    vi.stubEnv('VITE_DASHBOARD_API_KEY', 'baked')
    expect(dashboardHeaders()).toEqual({})
    vi.unstubAllEnvs()
  })

  it('asks for the key when the backend answers 401 and remembers it for the tab', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }))
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    render(<Dashboard />)
    fireEvent.change(await screen.findByLabelText('Dashboard API key'), { target: { value: ' secret ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Unlock' }))
    expect(getDashboardKey()).toBe('secret')
    expect(reload).toHaveBeenCalled()
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
    expect(fetchMock.mock.calls.some(([url]) => /\/api\/health$/.test(String(url)))).toBe(true)
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
    // The health probe, the notebook list and the governance audit page, each requested once on mount.
    expect(fetchMock).toHaveBeenCalledTimes(3)
    unmount()
    expect(signal.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
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

describe('dashboard credential handling', () => {
  const stubReload = () => {
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    return reload
  }

  it('attaches the key only to dashboard API requests, never to other URLs', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    setDashboardKey('s3cret')
    await dashboardFetch('/api/health')
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/health$/)
    expect(fetchMock.mock.calls[0][1].headers['X-API-Key']).toBe('s3cret')
    // code that calls the app, a static asset or another origin directly builds its own headers
    expect(JSON.stringify(fetchMock.mock.calls[0][1])).not.toContain('evil')
    render(<EndpointTester endpoints={['/add']} functions={[]} />)
    expect(document.body.innerHTML).not.toContain('s3cret')
  })

  it('reports a rejected key distinctly, never shows it, and lets the user replace it', async () => {
    setDashboardKey('old-key')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }))
    const reload = stubReload()
    render(<Dashboard />)
    expect(await screen.findByText(/API key rejected/)).toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain('old-key')
    fireEvent.change(screen.getByLabelText('Dashboard API key'), { target: { value: 'new-key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Replace key' }))
    expect(getDashboardKey()).toBe('new-key')
    expect(reload).toHaveBeenCalled()
  })

  it('forgets a stored key on request', async () => {
    setDashboardKey('keep-me-not')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'healthy' }) }))
    const reload = stubReload()
    render(<Dashboard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Forget key' }))
    expect(getDashboardKey()).toBe('')
    expect(reload).toHaveBeenCalled()
  })
})

describe('NotebookLibrary', () => {
  const stubList = (reply) => vi.stubGlobal('fetch', (url) => {
    expect(String(url)).toMatch(/\/api\/notebooks$/)
    return Promise.resolve(reply)
  })

  it('lists stored notebooks and opens the chosen one', async () => {
    stubList({
      ok: true,
      json: async () => ({ notebooks: [
        { filename: 'a.ipynb', size_bytes: 2048, currently_compiled: true },
        { filename: 'b.ipynb', size_bytes: 12 },
        { size_bytes: 1 },
      ] }),
    })
    const onOpen = vi.fn()
    render(<NotebookLibrary onOpen={onOpen} refreshKey={null} />)

    expect(await screen.findByText('a.ipynb')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByText('compiled')).toBeInTheDocument()
    expect(screen.getByText('12 B')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Open' })).toHaveLength(2)

    fireEvent.click(screen.getAllByRole('button', { name: 'Open' })[1])
    expect(onOpen).toHaveBeenCalledWith({ filename: 'b.ipynb' })
  })

  it('says so when nothing is stored and when the list cannot be loaded', async () => {
    stubList({ ok: true, json: async () => ({ notebooks: [] }) })
    const { unmount } = render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    expect(await screen.findByText('No notebooks uploaded yet')).toBeInTheDocument()
    unmount()

    stubList({ ok: false, status: 500, statusText: 'boom', json: async () => ({ detail: 'disk gone' }), text: async () => 'disk gone' })
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    expect(await screen.findByText(/disk gone|Could not list notebooks/)).toBeInTheDocument()
  })
})

describe('Diagnostics', () => {
  it('renders nothing without diagnostics', () => {
    const { container } = render(<Diagnostics diagnostics={diagnosticsFrom({})} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows skipped functions, inferred background endpoints and startup warnings', () => {
    render(<Diagnostics diagnostics={diagnosticsFrom({
      skipped_functions: [{ name: 'bad', reason: 'requires a callable' }, { reason: 'ignored: no name' }],
      inferred_background: ['train_x', 7],
      startup_warnings: ['Cell 1, line 2: input() blocks'],
    })} />)
    expect(screen.getByText('bad')).toBeInTheDocument()
    expect(screen.getByText(/requires a callable/)).toBeInTheDocument()
    expect(screen.getByText('train_x')).toBeInTheDocument()
    expect(screen.getByText(/inferred from their names/)).toBeInTheDocument()
    expect(screen.getByText('Cell 1, line 2: input() blocks')).toBeInTheDocument()
  })

  it('is filled from the inspect response and replaced by the compile response in the dashboard', async () => {
    const inspect = {
      functions: [{ name: 'train_x' }], endpoints: [{ path: '/train_x' }], generated_files: [],
      skipped_functions: [{ name: 'bad', reason: 'needs a callable' }], inferred_background: ['train_x'], startup_warnings: [],
    }
    const compile = { functions: [{ name: 'train_x' }], endpoints: [{ path: '/train_x' }], generated_files: ['app.py'],
      skipped_functions: [], inferred_background: [], startup_warnings: ['Ignored directive: # notebook-to-api: cahce 60'] }
    const replies = [{ filename: 'n.ipynb' }, inspect, compile]
    stubFetch(() => Promise.resolve({ ok: true, json: async () => replies.shift() }))
    const { container } = render(<Dashboard />)
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    expect(await screen.findByText(/needs a callable/)).toBeInTheDocument()
    expect(screen.getByText(/inferred from their names/)).toBeInTheDocument()
    fireEvent.click(screen.getByText(/Compile Now/))
    expect(await screen.findByText(/Ignored directive/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/needs a callable/)).toBeNull())
  })
})

describe('NotebookLibrary per-notebook app workflow', () => {
  const notebooks = [
    { filename: 'a.ipynb', size_bytes: 10, per_notebook_app: { status: 'compiled', notebook_changed_since_compile: false } },
    { filename: 'b.ipynb', size_bytes: 20, per_notebook_app: null },
    { filename: 'c.ipynb', size_bytes: 30, per_notebook_app: { status: 'compiled', notebook_changed_since_compile: true } },
  ]
  const ok = (body) => ({ ok: true, json: async () => body, blob: async () => new Blob(['zip']) })
  let saved

  const setup = (handler) => {
    saved = []
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() { saved.push(this.download) })
    const calls = []
    vi.stubGlobal('fetch', (url, init) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '')
      calls.push([path, init])
      return handler(path, init) ?? Promise.resolve(ok({ notebooks }))
    })
    return calls
  }

  afterEach(() => vi.restoreAllMocks())

  it('shows each notebook\'s own app state and only offers app actions where an app exists', async () => {
    setup(() => null)
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    expect(await screen.findByText('a.ipynb')).toBeInTheDocument()
    expect(screen.getByText('app ready')).toBeInTheDocument()
    expect(screen.getByText('app out of date')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Download app' })).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: 'Compile app' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Recompile app' })).toHaveLength(2)
  })

  it('compiles with per_notebook for the chosen notebook only and refreshes the list', async () => {
    const calls = setup((path) => (path === '/api/compile' ? Promise.resolve(ok({ status: 'success' })) : null))
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    await screen.findByText('b.ipynb')
    fireEvent.click(screen.getByRole('button', { name: 'Compile app' }))
    expect(await screen.findByText(/Compiled its own app/)).toBeInTheDocument()
    const compile = calls.find(([path]) => path === '/api/compile')
    expect(JSON.parse(compile[1].body)).toEqual({ notebook_path: 'b.ipynb', per_notebook: true })
    expect(calls.filter(([path]) => path === '/api/notebooks').length).toBeGreaterThanOrEqual(2)
  })

  it('downloads and exports through authenticated requests naming the notebook', async () => {
    const calls = setup((path) => {
      if (path.startsWith('/api/download')) return Promise.resolve(ok({}))
      if (path === '/api/export-openapi') return Promise.resolve(ok({ schema: { openapi: '3.1.0' } }))
      if (path === '/api/export-sdk') return Promise.resolve(ok({ code: 'class Client: ...' }))
      return null
    })
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    await screen.findByText('a.ipynb')
    fireEvent.click(screen.getAllByRole('button', { name: 'Download app' })[0])
    await screen.findByText(/Downloaded the app/)
    fireEvent.click(screen.getAllByRole('button', { name: 'OpenAPI' })[0])
    await screen.findByText(/Exported the OpenAPI schema/)
    fireEvent.click(screen.getAllByRole('button', { name: 'TypeScript SDK' })[0])
    await screen.findByText(/Exported the typescript client/)
    expect(saved).toEqual(['a-app.zip', 'a-openapi.json', 'a-client.ts'])
    expect(calls.some(([path]) => path === '/api/download?notebook=a.ipynb')).toBe(true)
    expect(JSON.parse(calls.find(([path]) => path === '/api/export-sdk')[1].body)).toEqual({ notebook: 'a.ipynb', language: 'typescript' })
  })

  it('deploys only after confirmation and reports success and failure per notebook', async () => {
    const calls = setup((path) => (path === '/api/deploy'
      ? Promise.resolve(calls_deploy.shift())
      : null))
    const calls_deploy = [
      ok({ status: 'success', tag: 'a:latest' }),
      { ok: false, status: 500, statusText: 'x', json: async () => ({ detail: 'Docker build failed: no daemon' }), text: async () => 'Docker build failed: no daemon' },
    ]
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    await screen.findByText('a.ipynb')
    const deploy = screen.getAllByRole('button', { name: 'Deploy' })
    fireEvent.click(deploy[0])
    expect(calls.some(([path]) => path === '/api/deploy')).toBe(false) // declined
    fireEvent.click(deploy[0])
    expect(await screen.findByText(/Built image a:latest/)).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Deploy' })[1])
    expect(await screen.findByText(/Docker build failed: no daemon/)).toBeInTheDocument()
    expect(screen.getByText(/Built image a:latest/)).toBeInTheDocument() // the first row keeps its own result
    expect(JSON.parse(calls.filter(([path]) => path === '/api/deploy')[1][1].body)).toEqual({ notebook: 'c.ipynb' })
  })

  it('shows a compile error on that notebook without disturbing the others', async () => {
    setup((path) => (path === '/api/compile'
      ? Promise.resolve({ ok: false, status: 400, statusText: 'bad', json: async () => ({ detail: 'not a valid notebook' }), text: async () => 'not a valid notebook' })
      : null))
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    await screen.findByText('b.ipynb')
    fireEvent.click(screen.getByRole('button', { name: 'Compile app' }))
    expect(await screen.findByText(/not a valid notebook/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Download app' })).toHaveLength(2)
  })
})

describe('per-notebook compilation and its diagnostics in the dashboard', () => {
  const ok = (body) => ({ ok: true, json: async () => body })
  const list = [
    { filename: 'a.ipynb', size_bytes: 1, per_notebook_app: null },
    { filename: 'b.ipynb', size_bytes: 2, per_notebook_app: null },
  ]

  const stub = (compileReplies) => {
    const calls = []
    vi.stubGlobal('fetch', (url, init) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '')
      calls.push([path, init])
      if (path === '/api/compile') return compileReplies.shift()
      if (path === '/api/notebooks') return Promise.resolve(ok({ notebooks: list }))
      if (path === '/api/health') return Promise.resolve(ok({ status: 'healthy' }))
      return Promise.resolve(ok({}))
    })
    return calls
  }

  it('the main Compile Now button compiles into the notebook\'s own app', async () => {
    const replies = [
      ok({ filename: 'n.ipynb' }),
      ok({ functions: [{ name: 'f' }], endpoints: [{ path: '/f' }], generated_files: [] }),
      ok({ functions: [{ name: 'f' }], endpoints: [{ path: '/f' }], generated_files: ['app.py'], dependencies: [] }),
    ]
    const calls = []
    vi.stubGlobal('fetch', (url, init) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '')
      calls.push([path, init])
      if (path === '/api/health') return Promise.resolve(ok({ status: 'healthy' }))
      if (path === '/api/notebooks') return Promise.resolve(ok({ notebooks: [] }))
      if (path.startsWith('/governance/audit')) return Promise.resolve(ok({ records: [], has_more: false, next_cursor: null }))
      return Promise.resolve(replies.shift())
    })
    const { container } = render(<Dashboard />)
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    fireEvent.click(await screen.findByText(/Compile Now/))
    await screen.findByText(/Compilation successful/)
    const compile = calls.find(([path]) => path === '/api/compile')
    expect(JSON.parse(compile[1].body)).toEqual({ notebook_path: 'n.ipynb', per_notebook: true })
  })

  it('links a finished compile to its governance audit record', async () => {
    const replies = [
      ok({ filename: 'n.ipynb' }),
      ok({ functions: [{ name: 'f' }], endpoints: [{ path: '/f' }], generated_files: [] }),
      ok({ functions: [{ name: 'f' }], endpoints: [{ path: '/f' }], generated_files: ['app.py'], dependencies: [], governance_audit: { sequence: 42 } }),
    ]
    vi.stubGlobal('fetch', (url) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, '')
      if (path === '/api/health') return Promise.resolve(ok({ status: 'healthy' }))
      if (path === '/api/notebooks') return Promise.resolve(ok({ notebooks: [] }))
      if (path.startsWith('/governance/audit')) return Promise.resolve(ok({ records: [], has_more: false, next_cursor: null }))
      return Promise.resolve(replies.shift())
    })
    const { container } = render(<Dashboard />)
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'n.ipynb')] } })
    fireEvent.click(await screen.findByText(/Compile Now/))
    expect(await screen.findByTestId('audit-link')).toHaveTextContent('#42')
  })

  it('shows skipped functions, inferred background endpoints and warnings under the notebook that was compiled', async () => {
    stub([Promise.resolve(ok({
      skipped_functions: [{ name: 'bad', reason: 'requires a callable' }],
      inferred_background: ['train_x'],
      startup_warnings: ['Cell 1, line 2: input() blocks the app at startup'],
    }))])
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    await screen.findByText('a.ipynb')
    fireEvent.click(screen.getAllByRole('button', { name: 'Compile app' })[0])
    const panel = await screen.findByTestId('diagnostics-a.ipynb')
    expect(panel).toHaveTextContent('bad')
    expect(panel).toHaveTextContent('requires a callable')
    expect(panel).toHaveTextContent('train_x')
    expect(panel).toHaveTextContent('input() blocks the app at startup')
    expect(screen.queryByTestId('diagnostics-b.ipynb')).toBeNull()
  })

  it('keeps each notebook\'s diagnostics separate and replaces them on its own recompile only', async () => {
    stub([
      Promise.resolve(ok({ skipped_functions: [{ name: 'only_a', reason: 'r1' }] })),
      Promise.resolve(ok({ startup_warnings: ['only b warning'] })),
      Promise.resolve(ok({ skipped_functions: [], inferred_background: [], startup_warnings: [] })),
    ])
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    await screen.findByText('a.ipynb')
    fireEvent.click(screen.getAllByRole('button', { name: 'Compile app' })[0])
    await screen.findByTestId('diagnostics-a.ipynb')
    fireEvent.click(screen.getAllByRole('button', { name: 'Compile app' })[1])
    await screen.findByTestId('diagnostics-b.ipynb')
    expect(screen.getByTestId('diagnostics-a.ipynb')).toHaveTextContent('only_a')
    expect(screen.getByTestId('diagnostics-a.ipynb')).not.toHaveTextContent('only b warning')
    expect(screen.getByTestId('diagnostics-b.ipynb')).not.toHaveTextContent('only_a')
    fireEvent.click(screen.getAllByRole('button', { name: 'Compile app' })[0])
    await waitFor(() => expect(screen.queryByTestId('diagnostics-a.ipynb')).toBeNull())
    expect(screen.getByTestId('diagnostics-b.ipynb')).toHaveTextContent('only b warning')
  })

  it('shows a loading state while compiling and an error, without stale diagnostics, when it fails', async () => {
    let finish
    stub([
      new Promise((resolve) => { finish = resolve }),
      Promise.resolve({ ok: false, status: 400, statusText: 'bad', json: async () => ({ detail: 'reserved name' }), text: async () => 'reserved name' }),
    ])
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} />)
    await screen.findByText('a.ipynb')
    fireEvent.click(screen.getAllByRole('button', { name: 'Compile app' })[0])
    const busy = await screen.findByRole('button', { name: 'Compiling...' })
    expect(busy).toBeDisabled()
    finish(ok({ skipped_functions: [{ name: 'stale', reason: 'x' }] }))
    await screen.findByTestId('diagnostics-a.ipynb')
    fireEvent.click(screen.getAllByRole('button', { name: /compile app/i })[0])
    expect(await screen.findByText(/reserved name/)).toBeInTheDocument()
    expect(screen.queryByTestId('diagnostics-a.ipynb')).toBeNull()
  })
})

describe('NotebookLibrary lifecycle actions', () => {
  const json = (body, status = 200, extra = {}) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...extra } })
  const click = (name) => fireEvent.click(screen.getByRole('button', { name }))

  const setup = async (handler) => {
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      const path = new URL(url, 'http://t').pathname
      calls.push({ path, method: init.method || 'GET' })
      if (path === '/api/notebooks' && (init.method || 'GET') === 'GET') return json({ notebooks: [{ filename: 'a.ipynb', size_bytes: 10 }], trash_count: 1 })
      return handler(path, init) ?? json({}, 404)
    }))
    const onAudit = vi.fn()
    render(<NotebookLibrary onOpen={() => {}} refreshKey="x" onAudit={onAudit} />)
    await screen.findByText('a.ipynb')
    return { calls, onAudit }
  }

  it('asks before deleting, then deletes and links the audit record', async () => {
    const { calls, onAudit } = await setup((path, init) => (init.method === 'DELETE' ? json({ status: 'success' }, 200, { 'X-Governance-Audit-Sequence': '7' }) : null))
    click('Delete a.ipynb')
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    click('Yes, delete a.ipynb')
    expect(await screen.findByTestId('library-event')).toHaveTextContent('Moved a.ipynb to the trash')
    fireEvent.click(screen.getByTestId('lifecycle-audit-link'))
    expect(onAudit).toHaveBeenCalledWith(7)
  })

  it('deletes into the trash and restores from it, explaining a name conflict', async () => {
    const item = { trash_id: 't1', filename: 'old.ipynb', deleted_at: '2026-01-01T00:00:00+00:00', expires_at: '2026-02-01T00:00:00+00:00' }
    let conflict = true
    const { calls } = await setup((path, init) => {
      if (path === '/api/notebooks/a.ipynb' && init.method === 'DELETE') return json({ status: 'success' })
      if (path === '/api/notebooks/trash') return json({ items: [item] })
      if (path === '/api/notebooks/trash/t1/restore') {
        if (conflict) return json({ detail: "A notebook named 'old.ipynb' already exists" }, 409)
        return json({ status: 'success' }, 200, { 'X-Governance-Audit-Sequence': '9' })
      }
      return null
    })
    click('Delete a.ipynb')
    click('Yes, delete a.ipynb')
    await screen.findByTestId('library-event')
    expect(calls.find((c) => c.method === 'DELETE').path).toBe('/api/notebooks/a.ipynb')
    click('Trash (1)')
    expect(await screen.findByText('old.ipynb')).toBeInTheDocument()
    click('Restore old.ipynb from the trash')
    expect(await screen.findByText(/Cannot restore old.ipynb: A notebook named/)).toBeInTheDocument()
    conflict = false
    click('Restore old.ipynb from the trash')
    expect(await screen.findByText(/Restored old.ipynb from the trash/)).toBeInTheDocument()
  })

  it('shows the server message for a rename conflict', async () => {
    await setup((path, init) => (init.method === 'PATCH' ? json({ detail: 'b.ipynb already exists' }, 409) : null))
    click('Rename a.ipynb')
    click('Rename')
    expect(await screen.findByRole('alert')).toHaveTextContent('already exists')
  })

  it('lists versions with data-file counts and restores only after confirmation', async () => {
    const versions = { versions: [{ version_id: 'v1.ipynb', size_bytes: 5, saved_at: '2026-01-01T00:00:00+00:00', companion_files: 2 }] }
    const { calls } = await setup((path, init) => {
      if (path.endsWith('/versions')) return json(versions)
      if (path.endsWith('/restore') && init.method === 'POST') return json({ status: 'success' }, 200, { 'X-Governance-Audit-Sequence': '9' })
      return null
    })
    click('Versions a.ipynb')
    expect(await screen.findByText('2 data files')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Restore the version of a.ipynb/ }))
    expect(calls.some((c) => c.path.endsWith('/restore'))).toBe(false)
    click('Confirm restore')
    expect(await screen.findByText(/Restored the version saved/)).toHaveTextContent('2 data files')
  })

  it('says so when there are no earlier versions', async () => {
    await setup((path) => (path.endsWith('/versions') ? json({ versions: [] }) : null))
    click('Versions a.ipynb')
    expect(await screen.findByText(/No earlier versions/)).toBeInTheDocument()
  })
})

describe('dashboard key scope', () => {
  const readOnly = { name: 'viewer', scope: 'read', authenticated: true, can: { read: true, compile: false, deploy: false, delete: false, rename: false, restore: false } }
  const stubNotebooks = () => vi.stubGlobal('fetch', (url) => Promise.resolve(
    /\/api\/notebooks$/.test(String(url))
      ? { ok: true, json: async () => ({ notebooks: [{ filename: 'a.ipynb', size_bytes: 3 }] }) }
      : { ok: true, json: async () => ({}) },
  ))

  it('disables compile, rename and delete for a read-only key and explains why', async () => {
    stubNotebooks()
    render(<NotebookLibrary onOpen={() => {}} refreshKey={null} caller={readOnly} />)
    await screen.findByText('a.ipynb')
    for (const name of [/Compile app/, /Rename a.ipynb/, /Delete a.ipynb/]) {
      const button = screen.getByRole('button', { name })
      expect(button).toBeDisabled()
      expect(button.getAttribute('title')).toMatch(/read scope cannot/)
    }
    expect(screen.getByRole('button', { name: /Versions a.ipynb/ })).toBeEnabled()
  })

  it('keeps every action enabled for an admin key or when the identity is unavailable', async () => {
    stubNotebooks()
    const admin = { ...readOnly, scope: 'admin', can: { ...readOnly.can, compile: true, delete: true, rename: true } }
    const { rerender } = render(<NotebookLibrary onOpen={() => {}} refreshKey={null} caller={admin} />)
    await screen.findByText('a.ipynb')
    expect(screen.getByRole('button', { name: /Delete a.ipynb/ })).toBeEnabled()
    rerender(<NotebookLibrary onOpen={() => {}} refreshKey={null} caller={null} />)
    expect(screen.getByRole('button', { name: /Delete a.ipynb/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /Compile app/ })).toBeEnabled()
  })

  it('reads the caller from /api/whoami and treats 401, 403 and bad answers as unknown', async () => {
    vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => readOnly }))
    expect((await fetchCaller()).scope).toBe('read')
    for (const reply of [{ ok: false, status: 401 }, { ok: false, status: 403 }, { ok: true, json: async () => ({ status: 'healthy' }) }]) {
      vi.stubGlobal('fetch', async () => reply)
      expect(await fetchCaller()).toBeNull()
    }
    vi.stubGlobal('fetch', async () => { throw new Error('down') })
    expect(await fetchCaller()).toBeNull()
    expect(allows(readOnly, 'delete')).toBe(false)
    expect(allows(null, 'delete')).toBe(true)
  })

  it('shows who is signed in and with which scope', async () => {
    vi.stubGlobal('fetch', (url) => Promise.resolve(
      /\/api\/health$/.test(String(url)) ? { ok: true, json: async () => ({ status: 'healthy' }) }
        : /\/api\/whoami$/.test(String(url)) ? { ok: true, json: async () => readOnly }
          : { ok: true, json: async () => ({ notebooks: [], records: [] }) },
    ))
    render(<Dashboard />)
    expect(await screen.findByTestId('caller')).toHaveTextContent('Signed in as viewer (read access)')
  })
})
