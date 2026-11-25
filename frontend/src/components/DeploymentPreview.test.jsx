import { afterEach, describe, expect, it, vi } from 'vitest'
import axe from 'axe-core'
import { fireEvent, render, screen } from '@testing-library/react'
import DeploymentPreview from './DeploymentPreview'

const reply = (body, ok = true, status = 200) => ({ ok, status, json: async () => body, headers: { get: () => null }, clone() { return this } })
const BODIES = {
  '/api/dockerfile-preview': { dockerfile: 'FROM python:3.13-slim\nUSER appuser\n' },
  '/api/docker-compose-preview': { docker_compose: 'services:\n  gen:\n    cap_drop: ["ALL"]\n    read_only: true\n' },
  '/api/k8s-preview': { kubernetes_manifest: 'securityContext:\n  readOnlyRootFilesystem: true\n' },
  '/api/env-example-preview': { env_example: '# NOTEBOOK_API_KEY=\n' },
}
const stub = (override = {}) => {
  const mock = vi.fn(async (url) => {
    const path = new URL(String(url), 'http://x').pathname
    return override[path] || reply(BODIES[path])
  })
  vi.stubGlobal('fetch', mock)
  return mock
}
afterEach(() => vi.unstubAllGlobals())
const open = () => fireEvent.click(screen.getByText('Preview deployment files'))

describe('DeploymentPreview', () => {
  it('stays closed and silent until opened, then shows the backend Dockerfile for the notebook', async () => {
    const mock = stub()
    const { container } = render(<DeploymentPreview notebook="my nb.ipynb" />)
    expect(mock).not.toHaveBeenCalled()
    open()
    expect((await screen.findByLabelText('Dockerfile contents')).textContent).toBe(BODIES['/api/dockerfile-preview'].dockerfile)
    expect(String(mock.mock.calls[0][0])).toContain('/api/dockerfile-preview?notebook_path=my%20nb.ipynb')
    expect(await (await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })).violations.map((v) => v.id)).toEqual([])
  })

  it('shows every file exactly as returned, including the hardening settings', async () => {
    stub()
    render(<DeploymentPreview />)
    open()
    await screen.findByLabelText('Dockerfile contents')
    for (const [tab, name, needle] of [['Docker Compose', 'docker-compose.yml', 'cap_drop'], ['Kubernetes', 'kubernetes.yaml', 'readOnlyRootFilesystem'], ['Environment', '.env.example', 'NOTEBOOK_API_KEY']]) {
      fireEvent.click(screen.getByRole('tab', { name: tab }))
      expect((await screen.findByLabelText(`${name} contents`)).textContent).toContain(needle)
    }
  })

  it('renders file contents as text, never as markup', async () => {
    stub({ '/api/dockerfile-preview': reply({ dockerfile: '<img src=x onerror=alert(1)>' }) })
    const { container } = render(<DeploymentPreview />)
    open()
    await screen.findByLabelText('Dockerfile contents')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByLabelText('Dockerfile contents').textContent).toBe('<img src=x onerror=alert(1)>')
  })

  it('shows an error for one file without hiding the others, and a missing field', async () => {
    stub({ '/api/docker-compose-preview': reply({ detail: 'no preview' }, false, 500), '/api/k8s-preview': reply({ status: 'success' }) })
    render(<DeploymentPreview />)
    open()
    await screen.findByLabelText('Dockerfile contents')
    fireEvent.click(screen.getByRole('tab', { name: 'Docker Compose' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/no preview/)
    fireEvent.click(screen.getByRole('tab', { name: 'Kubernetes' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/returned no Kubernetes/)
    fireEvent.click(screen.getByRole('tab', { name: 'Dockerfile' }))
    expect(screen.getByLabelText('Dockerfile contents')).toBeTruthy()
  })

  it('copies and downloads the shown file, and reports a copy failure', async () => {
    stub()
    const writeText = vi.fn().mockResolvedValueOnce().mockRejectedValueOnce(new Error('denied'))
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    const created = []
    URL.createObjectURL = vi.fn((blob) => { created.push(blob); return 'blob:x' })
    URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { created.push(this.download) })
    render(<DeploymentPreview />)
    open()
    await screen.findByLabelText('Dockerfile contents')
    fireEvent.click(screen.getByText('Copy'))
    expect(await screen.findByText('Copied Dockerfile')).toBeTruthy()
    expect(writeText).toHaveBeenCalledWith(BODIES['/api/dockerfile-preview'].dockerfile)
    fireEvent.click(screen.getByText('Download Dockerfile'))
    expect(created).toContain('Dockerfile')
    fireEvent.click(screen.getByText('Copy'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Could not copy/)
    click.mockRestore()
  })

  it('reloads the Dockerfile when the notebook changes', async () => {
    const mock = stub()
    const { rerender } = render(<DeploymentPreview notebook="a.ipynb" />)
    open()
    await screen.findByLabelText('Dockerfile contents')
    rerender(<DeploymentPreview notebook="b.ipynb" />)
    await vi.waitFor(() => expect(mock.mock.calls.some(([u]) => String(u).includes('b.ipynb'))).toBe(true))
  })
})
