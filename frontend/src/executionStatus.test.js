import { describe, expect, it } from 'vitest'

import { describeExecution } from './executionStatus'

const task = (status, httpStatus = 200) => describeExecution({ httpStatus, data: { task_id: 't1', status } })

describe('describeExecution', () => {
  it.each([
    ['processing', 'pending', /running \(not finished\)/],
    ['completed', 'success', /Task completed/],
    ['failed', 'failure', /Task failed/],
    ['cancelled', 'neutral', /Task cancelled/]
  ])('maps the backend task state %s', (state, tone, label) => {
    const result = task(state)
    expect(result.tone).toBe(tone)
    expect(result.label).toMatch(label)
  })

  it('never reports an unknown or missing task state as success', () => {
    for (const status of ['queued', 'weird', undefined, null, 7]) {
      const result = task(status)
      expect(result.tone).toBe('unknown')
      expect(result.icon).not.toBe('✅')
    }
  })

  it('only a failed or unavailable outcome can never carry the success icon', () => {
    for (const result of [task('failed'), describeExecution({ httpStatus: 500 }), describeExecution({ httpStatus: 404 }),
      describeExecution({ unavailable: true }), describeExecution({})]) {
      expect(result.icon).not.toBe('✅')
      expect(result.tone).not.toBe('success')
    }
  })

  it('maps plain HTTP outcomes', () => {
    expect(describeExecution({ httpStatus: 200, data: { result: 4 } })).toMatchObject({ tone: 'success', label: 'Succeeded' })
    expect(describeExecution({ httpStatus: 401 }).label).toBe('Not authorised')
    expect(describeExecution({ httpStatus: 503 }).label).toBe('Server error')
    expect(describeExecution({ httpStatus: 302 }).tone).toBe('unknown')
    expect(describeExecution({ unavailable: true, httpStatus: 200 }).label).toBe('Status unavailable')
  })
})
