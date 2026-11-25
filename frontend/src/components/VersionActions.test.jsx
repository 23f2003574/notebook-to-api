import { afterEach, describe, expect, it, vi } from 'vitest'
import axe from 'axe-core'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { VersionActions, VersionArchive } from './VersionActions'
import NotebookLibrary from './NotebookLibrary'

const reply = (body, ok = true, status = 200, extra = {}) => ({ ok, status, json: async () => body, blob: async () => new Blob(['zip']), headers: { get: () => null }, clone() { return this }, ...extra })
const VERSION = { version_id: 'v1', saved_at: '2026-11-24T10:00:00Z', size_bytes: 10, note: 'before refactor' }
afterEach(() => vi.unstubAllGlobals())

describe('VersionActions', () => {
  it('edits the note with PUT and refreshes', async () => {
    const fetchMock = vi.fn(async () => reply({ status: 'success' }))
    vi.stubGlobal('fetch', fetchMock)
    const onChanged = vi.fn()
    const { container } = render(<VersionActions filename="a.ipynb" version={VERSION} onChanged={onChanged} />)
    expect(screen.getByTestId('version-note').textContent).toContain('before refactor')
    fireEvent.click(screen.getByText('Edit note'))
    fireEvent.change(screen.getByLabelText(/^Note for the version/), { target: { value: 'ship it' } })
    expect((await (await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })).violations).map((v) => v.id)).toEqual([])
    fireEvent.click(screen.getByText('Save note'))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/notebooks\/a\.ipynb\/versions\/v1\/note$/)
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ note: 'ship it' })
  })

  it('shows a note error and keeps the editor', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'Note is too long' }, false, 400)))
    render(<VersionActions filename="a.ipynb" version={{ ...VERSION, note: '' }} />)
    fireEvent.click(screen.getByText('Add note'))
    fireEvent.click(screen.getByText('Save note'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Note is too long/)
    expect(screen.getByText('Save note')).toBeTruthy()
  })

  it('deletes only after an explicit confirmation', async () => {
    const fetchMock = vi.fn(async () => reply({ deleted_version_id: 'v1' }))
    vi.stubGlobal('fetch', fetchMock)
    const onChanged = vi.fn()
    render(<VersionActions filename="a.ipynb" version={VERSION} onChanged={onChanged} />)
    fireEvent.click(screen.getByLabelText(/^Delete the version saved/))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByRole('alertdialog')).toBeTruthy()
    fireEvent.click(screen.getByText('Yes, delete version'))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/versions\/v1$/)
  })

  it('disables note edit and delete for a key without the scope', () => {
    render(<VersionActions filename="a.ipynb" version={VERSION} canEdit={false} canDelete={false} refusal="Your key's read scope cannot delete" />)
    expect(screen.getByText('Edit note').disabled).toBe(true)
    expect(screen.getByLabelText(/^Delete the version saved/).disabled).toBe(true)
  })
})

describe('VersionArchive', () => {
  it('exports the archive as a download', async () => {
    const fetchMock = vi.fn(async () => reply({}))
    vi.stubGlobal('fetch', fetchMock)
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const names = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { names.push(this.download) })
    render(<VersionArchive filename="a.ipynb" />)
    fireEvent.click(screen.getByText('Export versions (.zip)'))
    expect(await screen.findByText(/Exported the notebook/)).toBeTruthy()
    expect(names).toEqual(['a-versions.zip'])
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/versions\/export$/)
    click.mockRestore()
  })

  it('imports with confirmation and posts the file as multipart with overwrite', async () => {
    const fetchMock = vi.fn(async () => reply({ status: 'success' }))
    vi.stubGlobal('fetch', fetchMock)
    const onImported = vi.fn()
    render(<VersionArchive filename="a.ipynb" onImported={onImported} />)
    const input = screen.getByLabelText(/Import archive/)
    fireEvent.change(input, { target: { files: [new File(['zip'], 'bundle.zip', { type: 'application/zip' })] } })
    fireEvent.click(screen.getByText('Import bundle.zip'))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByRole('alertdialog').textContent).toMatch(/replaces a\.ipynb and its entire version history/)
    fireEvent.click(screen.getByText('Yes, import'))
    await waitFor(() => expect(onImported).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/versions\/import\?overwrite=true$/)
    expect(init.method).toBe('POST')
    expect(init.body.get('file').name).toBe('bundle.zip')
  })

  it('says a rejected archive left the notebook unchanged', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ detail: 'Archive is corrupt' }, false, 400)))
    const onImported = vi.fn()
    render(<VersionArchive filename="a.ipynb" onImported={onImported} />)
    fireEvent.change(screen.getByLabelText(/Import archive/), { target: { files: [new File(['x'], 'bad.zip')] } })
    fireEvent.click(screen.getByText('Import bad.zip'))
    fireEvent.click(screen.getByText('Yes, import'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Archive is corrupt.*The notebook was not changed/)
    expect(onImported).not.toHaveBeenCalled()
  })

  it('cannot import without the overwrite scope', () => {
    render(<VersionArchive filename="a.ipynb" canImport={false} refusal="no" />)
    expect(screen.getByLabelText(/Import archive/).disabled).toBe(true)
  })
})

describe('library integration', () => {
  it('shows notes, delete and archive controls inside the versions panel and respects a read-only key', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => (String(url).includes('/versions')
      ? reply({ versions: [VERSION] })
      : reply({ notebooks: [{ filename: 'a.ipynb', size_bytes: 10, tags: [], description: '', per_notebook_app: null }] }))))
    const readOnly = { name: 'r', scope: 'read', authenticated: true, can: { read: true, delete: false, description: false, overwrite: false } }
    render(<NotebookLibrary caller={readOnly} onOpen={() => {}} refreshKey="x" onAudit={() => {}} />)
    fireEvent.click(await screen.findByLabelText('Versions a.ipynb'))
    expect(await screen.findByTestId('version-note')).toBeTruthy()
    expect(screen.getByLabelText(/^Delete the version saved/).disabled).toBe(true)
    expect(screen.getByLabelText(/Import archive/).disabled).toBe(true)
    expect(screen.getByText('Export versions (.zip)').disabled).toBe(false)
  })
})
