import assert from 'node:assert/strict'
import { test } from 'node:test'
import { copyToClipboard } from './clipboard.ts'

// Stubs globalThis[name], returning a restore function. Uses defineProperty (not a
// plain assignment) because Node 24's built-in `navigator` is an accessor with no
// setter — a bare assignment silently no-ops on it.
function setGlobal(name: string, value: unknown): () => void {
  const original = Object.getOwnPropertyDescriptor(globalThis, name)
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(globalThis, name, original)
    else delete (globalThis as Record<string, unknown>)[name]
  }
}

test('secure context: uses navigator.clipboard.writeText and resolves true', async () => {
  let calledWith: string | undefined
  const restoreWindow = setGlobal('window', { isSecureContext: true })
  const restoreNavigator = setGlobal('navigator', {
    clipboard: {
      writeText: async (t: string) => {
        calledWith = t
      },
    },
  })
  try {
    const result = await copyToClipboard('x')
    assert.equal(result, true)
    assert.equal(calledWith, 'x')
  } finally {
    restoreWindow()
    restoreNavigator()
  }
})

test('secure context but writeText throws: falls back to execCommand', async () => {
  const restoreWindow = setGlobal('window', { isSecureContext: true })
  const restoreNavigator = setGlobal('navigator', {
    clipboard: {
      writeText: async () => {
        throw new Error('denied')
      },
    },
  })
  let execCommandArg: string | undefined
  const fakeTextarea = { value: '', style: {} as Record<string, string>, focus: () => {}, select: () => {} }
  const restoreDocument = setGlobal('document', {
    createElement: () => fakeTextarea,
    body: {
      appendChild: () => {},
      removeChild: () => {},
    },
    execCommand: (cmd: string) => {
      execCommandArg = cmd
      return true
    },
  })
  try {
    const result = await copyToClipboard('x')
    assert.equal(result, true)
    assert.equal(execCommandArg, 'copy')
    assert.equal(fakeTextarea.value, 'x')
  } finally {
    restoreWindow()
    restoreNavigator()
    restoreDocument()
  }
})

test('HTTP (non-secure context): falls back to a hidden textarea + execCommand', async () => {
  const restoreWindow = setGlobal('window', { isSecureContext: false })
  const restoreNavigator = setGlobal('navigator', {})
  let execCommandArg: string | undefined
  const appended: unknown[] = []
  const removed: unknown[] = []
  const fakeTextarea = { value: '', style: {} as Record<string, string>, focus: () => {}, select: () => {} }
  const restoreDocument = setGlobal('document', {
    createElement: () => fakeTextarea,
    body: {
      appendChild: (el: unknown) => appended.push(el),
      removeChild: (el: unknown) => removed.push(el),
    },
    execCommand: (cmd: string) => {
      execCommandArg = cmd
      return true
    },
  })
  try {
    const result = await copyToClipboard('x')
    assert.equal(result, true)
    assert.equal(execCommandArg, 'copy')
    assert.equal(fakeTextarea.value, 'x')
    assert.equal(fakeTextarea.style.position, 'fixed')
    assert.equal(fakeTextarea.style.opacity, '0')
    assert.equal(fakeTextarea.style.pointerEvents, 'none')
    assert.equal(appended.length, 1)
    assert.equal(removed.length, 1)
  } finally {
    restoreWindow()
    restoreNavigator()
    restoreDocument()
  }
})

test('fallback failure (execCommand returns false): resolves false', async () => {
  const restoreWindow = setGlobal('window', { isSecureContext: false })
  const restoreNavigator = setGlobal('navigator', {})
  const fakeTextarea = { value: '', style: {} as Record<string, string>, focus: () => {}, select: () => {} }
  const restoreDocument = setGlobal('document', {
    createElement: () => fakeTextarea,
    body: {
      appendChild: () => {},
      removeChild: () => {},
    },
    execCommand: () => false,
  })
  try {
    const result = await copyToClipboard('x')
    assert.equal(result, false)
  } finally {
    restoreWindow()
    restoreNavigator()
    restoreDocument()
  }
})

test('fallback failure (execCommand throws): resolves false', async () => {
  const restoreWindow = setGlobal('window', { isSecureContext: false })
  const restoreNavigator = setGlobal('navigator', {})
  const fakeTextarea = { value: '', style: {} as Record<string, string>, focus: () => {}, select: () => {} }
  const restoreDocument = setGlobal('document', {
    createElement: () => fakeTextarea,
    body: {
      appendChild: () => {},
      removeChild: () => {},
    },
    execCommand: () => {
      throw new Error('boom')
    },
  })
  try {
    const result = await copyToClipboard('x')
    assert.equal(result, false)
  } finally {
    restoreWindow()
    restoreNavigator()
    restoreDocument()
  }
})

test('no document available: resolves false without throwing', async () => {
  const restoreWindow = setGlobal('window', { isSecureContext: false })
  const restoreNavigator = setGlobal('navigator', {})
  const restoreDocument = setGlobal('document', undefined)
  try {
    const result = await copyToClipboard('x')
    assert.equal(result, false)
  } finally {
    restoreWindow()
    restoreNavigator()
    restoreDocument()
  }
})
