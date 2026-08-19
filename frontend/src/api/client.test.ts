import assert from 'node:assert/strict'
import { afterEach, mock, test } from 'node:test'
import { ApiError, api, setTimeoutMessage, setUnauthorizedHandler } from './client.ts'

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

test('the backstop timeout aborts a hung request and surfaces a 408 ApiError (FE-M1)', async () => {
  mock.timers.enable({ apis: ['setTimeout'] })
  const orig = globalThis.fetch
  // A backend that never responds on its own — only the abort signal ends the fetch, exactly
  // as the browser behaves when our AbortController fires.
  globalThis.fetch = ((_url: string, init: RequestInit) =>
    new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () =>
        reject(new DOMException('The operation was aborted.', 'AbortError')),
      )
    })) as typeof fetch
  setTimeoutMessage('timed out!')
  const pending = api.status()
  mock.timers.tick(45000) // advance past DEFAULT_TIMEOUT_MS so the backstop fires
  await assert.rejects(
    pending,
    (e) => e instanceof ApiError && e.status === 408 && e.message === 'timed out!',
  )
  globalThis.fetch = orig
  mock.timers.reset()
  setTimeoutMessage('The request timed out.')
})

// --- what actually goes on the wire, and what comes back ---------------------
//
// The stub above ignores the URL, so nothing proved a method calls the right endpoint,
// carries a JSON body, or decodes the backend's error shapes. That decoding is how every
// 422 reaches the UI.

type Recorded = { url: string; method: string; headers: Headers; body?: string }

function recordFetch(response: Response): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = []
  const orig = globalThis.fetch
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({
      url,
      method: String(init.method),
      headers: new Headers(init.headers),
      body: init.body as string | undefined,
    })
    return response.clone()
  }) as unknown as typeof fetch
  return { calls, restore: () => { globalThis.fetch = orig } }
}

const jsonResponse = (status: number, payload: unknown) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

test('a GET carries no body and no content type', async () => {
  const { calls, restore } = recordFetch(jsonResponse(200, { scheduler_enabled: true }))
  try {
    await api.status()
    assert.equal(calls[0].url, '/api/status')
    assert.equal(calls[0].method, 'GET')
    assert.equal(calls[0].body, undefined)
    assert.equal(calls[0].headers.get('content-type'), null)
  } finally {
    restore()
  }
})

test('a body is JSON-encoded and announced as JSON', async () => {
  // Without the header FastAPI reads the body as a form and answers 422 for everything.
  const { calls, restore } = recordFetch(jsonResponse(200, { username: 'admin' }))
  try {
    await api.login('admin', 'secret12')
    assert.equal(calls[0].url, '/api/login')
    assert.equal(calls[0].method, 'POST')
    assert.equal(calls[0].headers.get('content-type'), 'application/json')
    assert.deepEqual(JSON.parse(calls[0].body as string), {
      username: 'admin',
      password: 'secret12',
    })
  } finally {
    restore()
  }
})

test('a string detail becomes the error message', async () => {
  const { restore } = recordFetch(jsonResponse(409, { detail: 'route is already running' }))
  try {
    await assert.rejects(
      () => api.status(),
      (e: unknown) =>
        e instanceof ApiError && e.status === 409 && e.message === 'route is already running',
    )
  } finally {
    restore()
  }
})

test('a structured detail prefers its message and keeps the raw payload', async () => {
  // The YAML editor's 422 shape: the modal shows `message` and uses `line` to place the
  // marker, so both halves have to survive.
  const detail = { message: 'invalid schedule.cron', line: 12 }
  const { restore } = recordFetch(jsonResponse(422, { detail }))
  try {
    await assert.rejects(
      () => api.status(),
      (e: unknown) => {
        assert.ok(e instanceof ApiError)
        assert.equal(e.message, 'invalid schedule.cron')
        assert.deepEqual(e.raw, detail)
        return true
      },
    )
  } finally {
    restore()
  }
})

test('a structured detail with no message falls back to its JSON', async () => {
  const detail = [{ loc: ['body', 'host'], msg: 'field required' }]
  const { restore } = recordFetch(jsonResponse(422, { detail }))
  try {
    await assert.rejects(
      () => api.status(),
      (e: unknown) => e instanceof ApiError && e.message === JSON.stringify(detail),
    )
  } finally {
    restore()
  }
})

test('a non-JSON error body keeps the status text', async () => {
  const { restore } = recordFetch(
    new Response('<html>502 Bad Gateway</html>', { status: 502, statusText: 'Bad Gateway' }),
  )
  try {
    await assert.rejects(
      () => api.status(),
      (e: unknown) => e instanceof ApiError && e.message === 'Bad Gateway',
    )
  } finally {
    restore()
  }
})

test('a 204 resolves to nothing instead of failing to parse an empty body', async () => {
  // DELETE /config/api-key answers 204; parsing "" as JSON would throw over a success.
  const { restore } = recordFetch(new Response(null, { status: 204 }))
  try {
    assert.equal(await api.logout(), undefined)
  } finally {
    restore()
  }
})
