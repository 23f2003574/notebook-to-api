import { describe, expect, it } from 'vitest'

import { errorFromResponse, messageFromBody } from './errorMessage'

const response = (status, body, bad = false) => ({
  status,
  json: async () => {
    if (bad) throw new SyntaxError('Unexpected token <')
    return body
  },
})

describe('messageFromBody', () => {
  it('returns a string detail', () => {
    expect(messageFromBody({ detail: 'Notebook file not found' })).toBe('Notebook file not found')
  })

  it('turns FastAPI validation errors into field: message pairs', () => {
    const detail = [
      { loc: ['body', 'notebook_path'], msg: 'Field required', type: 'missing' },
      { loc: ['body', 'options', 0], msg: 'Input should be a string', type: 'string_type' },
    ]
    expect(messageFromBody({ detail })).toBe(
      'notebook_path: Field required; options.0: Input should be a string'
    )
  })

  it('handles an object detail and nested or odd structures safely', () => {
    expect(messageFromBody({ detail: { msg: 'bad thing' } })).toBe('bad thing')
    expect(messageFromBody({ detail: { message: 'other' } })).toBe('other')
    expect(messageFromBody({ detail: [null, 5, {}, { loc: [] }] }, 'fb')).toBe('fb')
    expect(messageFromBody({ detail: { deep: { er: 1 } } }, 'fb')).toBe('fb')
  })

  it('falls back for empty, missing or non-object bodies', () => {
    for (const body of [undefined, null, 'oops', 42, {}, { detail: '' }, { detail: [] }]) {
      expect(messageFromBody(body)).toBe('Unknown error')
    }
  })

  it('never shows a traceback and caps very long messages', () => {
    const text = 'Boom happened\nTraceback (most recent call last):\n  File "/srv/app.py", line 9'
    expect(messageFromBody({ detail: text })).toBe('Boom happened')
    expect(messageFromBody({ detail: 'x'.repeat(2000) }).length).toBe(300)
  })
})

describe('errorFromResponse', () => {
  it('reads the detail from the response body', async () => {
    expect(await errorFromResponse(response(404, { detail: 'nope' }), 'Upload failed')).toBe('nope')
  })

  it('names the status when the body is empty or not JSON', async () => {
    expect(await errorFromResponse(response(502, {}), 'Upload failed')).toBe('Upload failed (HTTP 502)')
    expect(await errorFromResponse(response(500, null, true), 'Upload failed')).toBe('Upload failed (HTTP 500)')
    expect(await errorFromResponse({}, 'Upload failed')).toBe('Upload failed')
  })
})
