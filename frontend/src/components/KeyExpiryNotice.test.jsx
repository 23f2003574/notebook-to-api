import { describe, expect, it } from 'vitest'
import axe from 'axe-core'
import { render, screen } from '@testing-library/react'
import KeyExpiryNotice from './KeyExpiryNotice'
import { keyExpiryMessage } from '../keyExpiry'

const caller = (extra) => ({ authenticated: true, name: 'alice', scope: 'admin', can: {}, ...extra })

describe('KeyExpiryNotice', () => {
  it('warns when the key expires within 14 days and says how long is left', () => {
    expect(keyExpiryMessage(caller({ expires_in_days: 14, expires: '2026-12-10' }))).toMatch(/in 14 days \(2026-12-10\)/)
    expect(keyExpiryMessage(caller({ expires_in_days: 1 }))).toMatch(/in 1 day/)
    expect(keyExpiryMessage(caller({ expires_in_days: 0 }))).toMatch(/today/)
  })

  it('stays silent for keys without an expiry, distant expiries and untrusted answers', () => {
    expect(keyExpiryMessage(caller({ expires: null, expires_in_days: null }))).toBeNull()
    expect(keyExpiryMessage(caller({ expires_in_days: 15 }))).toBeNull()
    expect(keyExpiryMessage(caller({ expires_in_days: -2 }))).toBeNull()
    expect(keyExpiryMessage(caller({ expires_in_days: '3' }))).toBeNull()
    expect(keyExpiryMessage(null)).toBeNull()
    expect(keyExpiryMessage({ authenticated: false, expires_in_days: 3 })).toBeNull()
  })

  it('renders an accessible status and never shows key material', async () => {
    const { container } = render(<KeyExpiryNotice caller={caller({ expires_in_days: 3, expires: '2026-11-29', key: 'secret-value' })} />)
    expect(screen.getByRole('status')).toHaveTextContent('expires in 3 days')
    expect(container.textContent).not.toContain('secret-value')
    const violations = (await axe.run(container, { rules: { 'color-contrast': { enabled: false } } })).violations
    expect(violations.map((v) => v.id)).toEqual([])
  })

  it('renders nothing when there is no warning', () => {
    const { container } = render(<KeyExpiryNotice caller={caller({ expires_in_days: null })} />)
    expect(container).toBeEmptyDOMElement()
  })
})
