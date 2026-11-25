import { afterEach, describe, expect, it, vi } from 'vitest'
import axe from 'axe-core'
import { fireEvent, render, screen } from '@testing-library/react'
import NotebookValidation from './NotebookValidation'
import LibraryValidation from './LibraryValidation'
import NotebookLibrary from './NotebookLibrary'
import { validationIssues } from '../validationIssues'

const reply = (body, ok = true, status = 200) => ({ ok, status, json: async () => body, headers: { get: () => null }, clone() { return this } })
const BAD = {
  status: 'fail', reserved_name_conflicts: ['health_check'], duplicate_functions: ['g'],
  skipped_functions: [{ name: 'f', reason: 'requires *args' }], requirements_conflict: null,
  unrecognized_directives: [{ directive: 'bogus', line: '# notebook-to-api: bogus' }],
  import_time_hazards: [{ kind: 'exit_call', call: 'sys.exit', cell: 1, line: 11 }], cells_with_errors: [2],
  ignored_cache_directives: [], no_endpoints: false, startup_warnings: ['Cell 1, line 11: `sys.exit()` ends the process'],
}
afterEach(() => vi.unstubAllGlobals())
const axeIds = async (container) => (await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })).violations.map((v) => v.id)

describe('validationIssues', () => {
  it('words errors and warnings from the backend result and drops nothing silently', () => {
    const issues = validationIssues(BAD)
    expect(issues.filter((i) => i.severity === 'error').map((i) => i.text)).toEqual([expect.stringContaining('"health_check"')])
    const text = issues.map((i) => i.text).join('\n')
    for (const part of ['"g"', 'requires *args', 'sys.exit runs when the app starts (cell 1, line 11)', '"bogus"', 'cell 2', 'ends the process']) {
      expect(text).toContain(part)
    }
  })

  it('is empty for a clean or malformed result', () => {
    expect(validationIssues({ status: 'pass', reserved_name_conflicts: [], skipped_functions: [] })).toEqual([])
    expect(validationIssues(null)).toEqual([])
    expect(validationIssues({ import_time_hazards: 'x', skipped_functions: 5 })).toEqual([])
  })
})

describe('NotebookValidation', () => {
  it('validates the named notebook, posts nothing else, and lists the issues', async () => {
    const fetchMock = vi.fn(async () => reply(BAD))
    vi.stubGlobal('fetch', fetchMock)
    const { container } = render(<NotebookValidation filename="a.ipynb" />)
    fireEvent.click(screen.getByText('Check before compiling'))
    expect(await screen.findByText(/Would fail to compile/)).toBeTruthy()
    expect(screen.getByText(/uses a name the generated app reserves/)).toBeTruthy()
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/validate$/)
    expect(JSON.parse(init.body)).toEqual({ notebook_path: 'a.ipynb' })
    expect(await axeIds(container)).toEqual([])
  })

  it('reports a clean notebook, a backend error and a key without the scope', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ status: 'pass' })))
    const { unmount } = render(<NotebookValidation filename="a.ipynb" />)
    fireEvent.click(screen.getByText('Check before compiling'))
    expect(await screen.findByText(/Would compile cleanly/)).toBeTruthy()
    expect(screen.getByText('No issues found.')).toBeTruthy()
    unmount()
    vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'Notebook not found' }, false, 404)))
    const second = render(<NotebookValidation filename="a.ipynb" />)
    fireEvent.click(screen.getByText('Check before compiling'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Notebook not found/)
    second.unmount()
    render(<NotebookValidation filename="a.ipynb" canValidate={false} />)
    expect(screen.getByText('Check before compiling').disabled).toBe(true)
  })
})

describe('LibraryValidation', () => {
  it('summarises the library and lists only notebooks that need attention', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({
      result_count: 3, pass_count: 1, warn_count: 1, fail_count: 1,
      results: [{ filename: 'ok.ipynb', status: 'pass' }, { filename: 'bad.ipynb', ...BAD }, { filename: 'meh.ipynb', status: 'warn', duplicate_functions: ['h'] }],
    })))
    const { container, rerender } = render(<LibraryValidation refreshKey="1" />)
    fireEvent.click(screen.getByText('Validate all notebooks'))
    expect(await screen.findByText('1 passing · 1 with warnings · 1 failing')).toBeTruthy()
    expect(screen.queryByText('ok.ipynb')).toBeNull()
    expect(screen.getByText('bad.ipynb')).toBeTruthy()
    expect(screen.getByText('meh.ipynb')).toBeTruthy()
    expect(await axeIds(container)).toEqual([])
    rerender(<LibraryValidation refreshKey="2" />)
    expect(screen.getByText(/library changed since this check/)).toBeTruthy()
  })

  it('shows backend failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'nope' }, false, 500)))
    render(<LibraryValidation refreshKey="1" />)
    fireEvent.click(screen.getByText('Validate all notebooks'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/nope/)
  })
})

describe('library integration', () => {
  it('each notebook has a Validate panel', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => (String(url).includes('/api/validate')
      ? reply({ status: 'warn', duplicate_functions: ['g'] })
      : reply({ notebooks: [{ filename: 'a.ipynb', size_bytes: 10, tags: [], description: '', per_notebook_app: null }] }))))
    render(<NotebookLibrary caller={null} onOpen={() => {}} refreshKey="x" onAudit={() => {}} />)
    fireEvent.click(await screen.findByLabelText('Validate a.ipynb'))
    fireEvent.click(screen.getByText('Check before compiling'))
    expect(await screen.findByText(/Would compile, with warnings/)).toBeTruthy()
  })
})
