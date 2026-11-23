import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import VersionCompare from './VersionCompare'
import { signature } from '../versionDiff'

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const versions = [
  { version_id: '20260102T000000000000_bbbbbbbb.ipynb', saved_at: '2026-01-02T00:00:00Z', size_bytes: 10 },
  { version_id: '20260101T000000000000_aaaaaaaa.ipynb', saved_at: '2026-01-01T00:00:00Z', size_bytes: 9 },
]
const fn = (name, args = [], ret = 'int') => ({ name, args, return_type: ret })

function mount(handler) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    calls.push(String(url))
    return handler(String(url))
  }))
  render(<VersionCompare filename="a.ipynb" versions={versions} />)
  return calls
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('VersionCompare', () => {
  it('formats a function signature with types and defaults', () => {
    expect(signature({ name: 'f', args: [{ name: 'a', type: 'int' }, { name: 'b', type: 'int', has_default: true, default: 1 }], return_type: 'int' }))
      .toBe('f(a: int, b: int = 1) -> int')
  })

  it('compares a version with the current notebook and shows added, removed and changed functions', async () => {
    const calls = mount(() => json({
      added: [fn('g')], removed: [fn('h')],
      changed: [{ name: 'f', old: fn('f', [{ name: 'a', type: 'int' }]), new: fn('f', [{ name: 'a', type: 'int' }, { name: 'b', type: 'int', has_default: true, default: 1 }]) }],
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions' }))
    const result = await screen.findByTestId('diff-a.ipynb')
    expect(calls[0]).toMatch(new RegExp('/api/notebooks/a.ipynb/versions/' + versions[0].version_id + '/diff$'))
    expect(screen.getByRole('region', { name: 'Added functions' })).toHaveTextContent('g() -> int')
    expect(screen.getByRole('region', { name: 'Removed functions' })).toHaveTextContent('h() -> int')
    expect(screen.getByRole('region', { name: 'Changed functions' })).toHaveTextContent('f(a: int) -> int → f(a: int, b: int = 1) -> int')
    expect(result).toBeInTheDocument()
  })

  it('compares two saved versions through the against parameter', async () => {
    const calls = mount(() => json({ added: [], removed: [], changed: [] }))
    fireEvent.change(screen.getByLabelText(/^with/), { target: { value: versions[1].version_id } })
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions' }))
    expect(await screen.findByText('No differences in the endpoint functions.')).toBeInTheDocument()
    expect(calls[0]).toContain(`/diff?against=${encodeURIComponent(versions[1].version_id)}`)
  })

  const sha = (c) => c.repeat(64)
  const file = (path, bytes, c) => ({ path, bytes, sha256: sha(c) })

  it('shows data files added, removed and changed between the versions', async () => {
    mount(() => json({
      added: [], removed: [], changed: [],
      files: {
        comparable: true, from_recorded: true, to_recorded: true,
        added: [file('new.csv', 3, 'a')], removed: [file('gone.csv', 5, 'b')],
        changed: [{ path: 'weights.json', from: file('weights.json', 4, 'c'), to: file('weights.json', 9, 'd') }],
      },
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions' }))
    expect(await screen.findByRole('region', { name: 'Added data files' })).toHaveTextContent('new.csv')
    expect(screen.getByRole('region', { name: 'Removed data files' })).toHaveTextContent('gone.csv')
    expect(screen.getByRole('region', { name: 'Changed data files' })).toHaveTextContent(/weights\.json\s*4 → 9 bytes/)
    expect(screen.getByText('No differences in the endpoint functions.')).toBeInTheDocument()
  })

  it('says so when the data files are identical or were not recorded for a version', async () => {
    mount(() => json({ added: [], removed: [], changed: [], files: { comparable: true, from_recorded: true, to_recorded: true, added: [], removed: [], changed: [] } }))
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions' }))
    expect(await screen.findByText('No differences in the data files.')).toBeInTheDocument()
    cleanup()
    mount(() => json({ added: [], removed: [], changed: [], files: { comparable: false, from_recorded: false, to_recorded: true, added: [], removed: [], changed: [] } }))
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions' }))
    expect(await screen.findByText(/Data files were not compared: the older version was saved before data files were versioned/)).toBeInTheDocument()
  })

  it('refuses to compare a version with itself', () => {
    mount(() => json({}))
    fireEvent.change(screen.getByLabelText(/^with/), { target: { value: versions[0].version_id } })
    expect(screen.getByRole('button', { name: 'Compare versions' })).toBeDisabled()
    expect(screen.getByText('Choose two different versions to compare.')).toBeInTheDocument()
  })

  it('announces a backend error', async () => {
    mount(() => json({ detail: 'Version not found' }, 404))
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Version not found')
  })

  it('lists data files, or says there are none, or reports an error', async () => {
    let reply = json({ files: [{ path: 'data.csv', bytes: 8 }] })
    mount(() => reply)
    fireEvent.click(screen.getByRole('button', { name: 'Show data files' }))
    expect(await screen.findByRole('list', { name: 'Data files' })).toHaveTextContent('data.csv')
    reply = json({ files: [] })
    fireEvent.click(screen.getByRole('button', { name: 'Show data files' }))
    expect(await screen.findByText('This notebook has no data files.')).toBeInTheDocument()
    reply = json({ detail: 'boom' }, 500)
    fireEvent.click(screen.getByRole('button', { name: 'Show data files' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})
