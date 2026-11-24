import { afterEach, describe, expect, it, vi } from 'vitest'
import axe from 'axe-core'
import { fireEvent, render, screen } from '@testing-library/react'
import NotebookLibrary from './NotebookLibrary'
import NotebookUpload from './NotebookUpload'
import OperationHistory from './OperationHistory'

const reply = (body) => ({
  ok: true, status: 200, headers: { get: () => null },
  json: async () => body, text: async () => JSON.stringify(body), clone() { return this },
})
const NOTEBOOKS = [{ filename: 'a.ipynb', size_bytes: 10, tags: ['prod'], description: 'first', per_notebook_app: null }]

// Violations axe reports for the rendered page, as readable strings. Colour contrast needs real layout and
// styles, which jsdom does not have, so that rule is left to a browser.
async function violations(container) {
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
}

afterEach(() => vi.unstubAllGlobals())

describe('accessibility (axe)', () => {
  it('the upload control, including the replace dialog, has no violations', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).endsWith('/api/upload')) return { ...reply({ detail: 'exists' }), ok: false, status: 409 }
      return { ...reply({}), headers: { get: () => 'abc' } }
    }))
    const { container } = render(<NotebookUpload onSuccess={() => {}} />)
    expect(await violations(container)).toEqual([])
    fireEvent.change(container.querySelector('input[type=file]'), { target: { files: [new File(['{}'], 'a.ipynb')] } })
    await screen.findByRole('alertdialog')
    expect(await violations(container)).toEqual([])
  })

  it('the notebook library with its details editor has no violations', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ notebooks: NOTEBOOKS, trash_count: 0, max_tag_length: 32 })))
    const { container } = render(<NotebookLibrary onOpen={() => {}} refreshKey="x" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Details a.ipynb' }))
    await screen.findByRole('form', { name: 'Details of a.ipynb' })
    expect(await violations(container)).toEqual([])
  })

  it('the history panel has no violations', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => reply({ entries: [{ compiled_at: '2026-01-01T00:00:00+00:00', notebook_filename: 'a.ipynb' }] })))
    const { container } = render(<OperationHistory />)
    fireEvent.click(screen.getByRole('button', { name: 'Show compile and deploy history' }))
    await screen.findByTestId('history-compile')
    expect(await violations(container)).toEqual([])
  })
})
