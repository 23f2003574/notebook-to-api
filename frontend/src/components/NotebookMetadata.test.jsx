import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import NotebookLibrary from './NotebookLibrary'
import NotebookMetadata from './NotebookMetadata'
import { metadataProblem } from '../notebookMetadata'

const reply = (body, status = 200) => ({
  ok: status < 400, status, headers: { get: () => null },
  json: async () => body, text: async () => JSON.stringify(body), clone() { return this },
})
const notebook = (extra = {}) => ({ filename: 'a.ipynb', size_bytes: 10, tags: ['prod'], description: 'first', ...extra })
const LIMITS = { max_tag_length: 5, max_tags_per_notebook: 2, max_description_length: 10 }

afterEach(() => vi.unstubAllGlobals())

describe('metadataProblem', () => {
  it('enforces the server limits and ignores unknown ones', () => {
    expect(metadataProblem(['a', 'b', 'c'], '', LIMITS)).toMatch(/At most 2 tags/)
    expect(metadataProblem(['toolong'], '', LIMITS)).toMatch(/longer than 5/)
    expect(metadataProblem([], 'x'.repeat(11), LIMITS)).toMatch(/description is longer than 10/)
    expect(metadataProblem(['a'], 'ok', LIMITS)).toBeNull()
    expect(metadataProblem(['anything-at-all'], 'x'.repeat(5000), {})).toBeNull()
  })
})

describe('NotebookMetadata', () => {
  const setup = (props = {}) => {
    const onSaved = vi.fn()
    render(<NotebookMetadata notebook={notebook()} limits={LIMITS} canEdit onSaved={onSaved} onClose={() => {}} {...props} />)
    return onSaved
  }

  it('adds and removes tags, edits the description and saves both, reporting what the server stored', async () => {
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
      calls.push([String(url), JSON.parse(init.body)])
      return String(url).endsWith('/tags') ? reply({ tags: ['dev'] }) : reply({ description: 'second' })
    }))
    const onSaved = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Remove tag prod from a.ipynb' }))
    fireEvent.change(screen.getByPlaceholderText('Add a tag'), { target: { value: 'dev' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add tag' }))
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'second' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save details' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ tags: ['dev'], description: 'second' }))
    expect(calls[0][0]).toMatch(/\/api\/notebooks\/a\.ipynb\/tags$/)
    expect(calls[0][1]).toEqual({ tags: ['dev'] })
    expect(calls[1][1]).toEqual({ description: 'second' })
  })

  it('blocks submission that breaks the limits without calling the API', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    setup()
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'x'.repeat(11) } })
    expect(screen.getByRole('alert')).toHaveTextContent('longer than 10')
    expect(screen.getByRole('button', { name: 'Save details' })).toBeDisabled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows the server error and keeps what was typed when saving fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'tag has invalid characters' }, 400)))
    const onSaved = setup()
    fireEvent.change(screen.getByPlaceholderText('Add a tag'), { target: { value: 'bad!' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add tag' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save details' }))
    expect(await screen.findByText(/tag has invalid characters/)).toBeInTheDocument()
    expect(onSaved).toHaveBeenCalledWith({ tags: ['prod'], description: 'first' })
    expect(screen.getByText('bad!', { exact: false })).toBeInTheDocument()
  })

  it('is read-only for a key that may not edit', () => {
    setup({ canEdit: false, refusal: "Your key's read scope cannot tags" })
    expect(screen.getByLabelText('Description')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save details' })).toBeDisabled()
    expect(screen.getByText(/read scope cannot tags/)).toBeInTheDocument()
  })
})

describe('NotebookLibrary tags', () => {
  const library = (caller) => vi.fn(async (url) => {
    const u = String(url)
    if (u.includes('/api/config')) return reply(LIMITS)
    if (u.includes('tag=prod')) return reply({ notebooks: [notebook()], trash_count: 0 })
    return reply({ notebooks: [notebook(), notebook({ filename: 'b.ipynb', tags: ['dev'], description: '' })], trash_count: 0, caller })
  })

  it('shows tags and descriptions and filters by tag through the server', async () => {
    const fetchMock = library()
    vi.stubGlobal('fetch', fetchMock)
    render(<NotebookLibrary onOpen={() => {}} refreshKey="x" />)
    expect(await screen.findByTestId('description-a.ipynb')).toHaveTextContent('first')
    expect(screen.getByText('b.ipynb')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Filter by tag'), { target: { value: 'prod' } })
    await waitFor(() => expect(screen.queryByText('b.ipynb')).not.toBeInTheDocument())
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/api/notebooks?tag=prod'))).toBe(true)
  })

  it('opens the details editor, disabled for a read-only key', async () => {
    vi.stubGlobal('fetch', library())
    const caller = { name: 'viewer', scope: 'read', can: { tags: false, description: false } }
    render(<NotebookLibrary onOpen={() => {}} refreshKey="x" caller={caller} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Details a.ipynb' }))
    expect(await screen.findByRole('form', { name: 'Details of a.ipynb' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save details' })).toBeDisabled()
  })
})
