import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import EndpointTester from './components/EndpointTester'

afterEach(() => vi.unstubAllGlobals())

const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

function setup(fetchMock) {
  vi.stubGlobal('fetch', fetchMock)
  const view = render(<EndpointTester endpoints={['/work']} functions={[]} />)
  fireEvent.change(view.container.querySelector('select'), { target: { value: '/work' } })
  const send = async () => {
    fireEvent.click(screen.getByText(/Send Request/))
    await waitFor(() => expect(screen.queryByText('Sending...')).toBeNull())
  }
  return { send, ...view }
}
const status = () => screen.getByRole('status')

describe('endpoint tester execution status', () => {
  it('shows a submitted background task as running, not as a success', async () => {
    const { send } = setup(vi.fn().mockResolvedValue(reply(200, { task_id: 't1', status: 'processing' })))
    await send()
    expect(status()).toHaveTextContent('Task running (not finished)')
    expect(status()).not.toHaveTextContent('completed')
    expect(status().textContent).not.toContain('✅')
  })

  it('refreshing the task updates the displayed state and sends the key and task id', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(200, { task_id: 't 1', status: 'processing' }))
      .mockResolvedValueOnce(reply(200, { status: 'completed', result: 8 }))
    const { send } = setup(fetchMock)
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'k-1' } })
    await send()
    fireEvent.click(screen.getByText(/Refresh task status/))
    await waitFor(() => expect(status()).toHaveTextContent('Task completed'))
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/tasks\/t%201$/)
    expect(fetchMock.mock.calls[1][1].headers['X-API-Key']).toBe('k-1')
    expect(screen.getByText(/"result": 8/)).toBeInTheDocument()
  })

  it('a failed task never shows a completion indicator', async () => {
    const { send } = setup(vi.fn().mockResolvedValue(reply(200, { task_id: 't1', status: 'failed', error: 'boom' })))
    await send()
    expect(status()).toHaveTextContent('Task failed')
    expect(status().textContent).not.toMatch(/✅|completed|Succeeded/)
  })

  it('an unknown task state is shown as unknown', async () => {
    const { send } = setup(vi.fn().mockResolvedValue(reply(200, { task_id: 't1', status: 'queued-ish' })))
    await send()
    expect(status()).toHaveTextContent('Unknown task state: queued-ish')
  })

  it('an HTTP error is a failure with its code', async () => {
    const { send } = setup(vi.fn().mockResolvedValue(reply(500, { detail: 'x' })))
    await send()
    expect(status()).toHaveTextContent('Server error')
    expect(screen.getByText('HTTP 500')).toBeInTheDocument()
  })

  it('a later network failure does not leave the previous success badge in place', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(200, { result: 4 }))
      .mockRejectedValueOnce(new Error('connection refused'))
    const { send } = setup(fetchMock)
    await send()
    expect(status()).toHaveTextContent('Succeeded')
    await send()
    expect(status()).toHaveTextContent('Status unavailable')
    expect(status()).not.toHaveTextContent('Succeeded')
    expect(screen.queryByText('HTTP 200')).toBeNull()
  })

  it('an unreachable app while refreshing says so and keeps the last known state visible', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(200, { task_id: 't1', status: 'processing' }))
      .mockRejectedValueOnce(new Error('down'))
    const { send } = setup(fetchMock)
    await send()
    fireEvent.click(screen.getByText(/Refresh task status/))
    await waitFor(() => expect(status()).toHaveTextContent('Status unavailable'))
    expect(screen.getByText(/Showing the last known state/)).toBeInTheDocument()
  })
})
