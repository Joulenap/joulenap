import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { ApiError, api, setUnauthorizedHandler } from './client.ts'

// Make every request resolve to the given status, ignoring the URL (req() uses the global
// fetch). Returns a restore function.
function stubFetch(status: number): () => void {
  const orig = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: 'nope' }), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })) as typeof fetch
  return () => {
    globalThis.fetch = orig
  }
}

afterEach(() => setUnauthorizedHandler(null))

test('a 401 on a session-protected endpoint triggers the unauthorized handler', async () => {
  const restore = stubFetch(401)
  let fired = false
  setUnauthorizedHandler(() => {
    fired = true
  })
  await assert.rejects(() => api.status(), (e) => e instanceof ApiError && e.status === 401)
  assert.equal(fired, true, 'expired session must route back to login')
  restore()
})

test('a 401 on /account does NOT trigger the handler (wrong current password, BE-S9)', async () => {
  // Would eject the user mid-form if the handler fired on every 401 — the exact bug the
  // exempt-path set prevents.
  const restore = stubFetch(401)
  let fired = false
  setUnauthorizedHandler(() => {
    fired = true
  })
  await assert.rejects(
    () => api.updateAccount('wrong-current', 'admin'),
    (e) => e instanceof ApiError && e.status === 401,
  )
  assert.equal(fired, false)
  restore()
})

test('a 401 on /login does NOT trigger the handler (wrong credentials)', async () => {
  const restore = stubFetch(401)
  let fired = false
  setUnauthorizedHandler(() => {
    fired = true
  })
  await assert.rejects(
    () => api.login('admin', 'bad'),
    (e) => e instanceof ApiError && e.status === 401,
  )
  assert.equal(fired, false)
  restore()
})
