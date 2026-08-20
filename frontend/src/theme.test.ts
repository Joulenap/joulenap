import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

// The light theme is a second block of --jn-* definitions in index.css. The most likely
// future regression is adding a token to one palette and forgetting the other, which
// silently renders as the fallback/inherited value — so parity is enforced here.

const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')

function varsIn(block: string): Set<string> {
  return new Set([...block.matchAll(/--jn-[\w-]+(?=\s*:)/g)].map((m) => m[0]))
}

const darkBlock = css.match(/:root\s*\{[^}]*\}/)?.[0] ?? ''
const lightBlock = css.match(/:root\[data-theme='light'\]\s*\{[^}]*\}/)?.[0] ?? ''

test('both theme palettes exist and define the same token set', () => {
  const dark = varsIn(darkBlock)
  const light = varsIn(lightBlock)
  assert.ok(dark.size > 0, 'dark :root block with --jn-* tokens not found')
  assert.deepEqual([...dark].sort(), [...light].sort())
})

test('every var(--jn-*) referenced anywhere in src is defined in the palette', () => {
  const dark = varsIn(darkBlock)
  // Walked, not listed: the hardcoded six-file version missed six real source files that
  // reference tokens, and a token defined in neither palette renders as the inherited
  // value rather than failing, so nothing else would catch it.
  const root = fileURLToPath(new URL('.', import.meta.url))
  const files = readdirSync(root, { recursive: true, withFileTypes: true })
    .filter((e) => e.isFile() && /\.(ts|tsx|css)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name))
    .map((e) => join(e.parentPath, e.name))

  assert.ok(files.length > 20, `expected to walk the whole tree, found ${files.length} files`)

  for (const file of files) {
    const src = readFileSync(file, 'utf8')
    for (const m of src.matchAll(/var\((--jn-[\w-]+)\)/g)) {
      assert.ok(dark.has(m[1]), `${file} references ${m[1]} which index.css does not define`)
    }
  }
})

// --- applyTheme / currentTheme ------------------------------------------------
//
// theme.ts was read as text above but never imported, so its two functions had no
// coverage at all. There is no DOM here, so document and localStorage are stubbed the way
// clipboard.test.ts stubs its globals.

function stub(name: string, value: unknown): () => void {
  const original = Object.getOwnPropertyDescriptor(globalThis, name)
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(globalThis, name, original)
    else delete (globalThis as Record<string, unknown>)[name]
  }
}

function fakeDom(initial?: string) {
  const classes = new Set<string>()
  const meta = { content: '' }
  const documentElement = {
    dataset: initial ? { theme: initial } : ({} as Record<string, string>),
    classList: {
      add: (c: string) => classes.add(c),
      remove: (c: string) => classes.delete(c),
    },
  }
  return {
    documentElement,
    classes,
    meta,
    querySelector: (selector: string) =>
      selector.includes('theme-color') ? { setAttribute: (_k: string, v: string) => (meta.content = v) } : null,
  }
}

test('currentTheme reads the stamped attribute and defaults to dark', async () => {
  const { currentTheme } = await import('./theme.ts')

  let restore = stub('document', fakeDom('light'))
  try {
    assert.equal(currentTheme(), 'light')
  } finally {
    restore()
  }

  restore = stub('document', fakeDom())
  try {
    // Nothing stamped means dark: the app is dark-first, and index.html stamps before paint.
    assert.equal(currentTheme(), 'dark')
  } finally {
    restore()
  }
})

test('applyTheme stamps the root, updates the tab colour and remembers the choice', async () => {
  const { applyTheme } = await import('./theme.ts')
  const dom = fakeDom('dark')
  const stored: Record<string, string> = {}
  const restoreDoc = stub('document', dom)
  const restoreStorage = stub('localStorage', {
    setItem: (k: string, v: string) => (stored[k] = v),
  })
  try {
    applyTheme('light')

    assert.equal(dom.documentElement.dataset.theme, 'light')
    // The browser chrome has to follow, or the tab keeps the other theme's colour.
    assert.equal(dom.meta.content, '#f3f4f6')
    assert.equal(stored.jnTheme, 'light')
    assert.ok(dom.classes.has('theme-fade')) // the cross-fade is armed on a real change
  } finally {
    restoreDoc()
    restoreStorage()
  }
})

test('applyTheme does not cross-fade when the theme is unchanged', async () => {
  // Re-applying the current theme (a config reload) should not flash the whole page.
  const { applyTheme } = await import('./theme.ts')
  const dom = fakeDom('dark')
  const restoreDoc = stub('document', dom)
  const restoreStorage = stub('localStorage', { setItem: () => {} })
  try {
    applyTheme('dark')
    assert.equal(dom.classes.has('theme-fade'), false)
    assert.equal(dom.documentElement.dataset.theme, 'dark')
  } finally {
    restoreDoc()
    restoreStorage()
  }
})

test('applyTheme still applies when storage is unavailable', async () => {
  // Private browsing throws on setItem; the theme must still change for the session.
  const { applyTheme } = await import('./theme.ts')
  const dom = fakeDom('dark')
  const restoreDoc = stub('document', dom)
  const restoreStorage = stub('localStorage', {
    setItem: () => {
      throw new Error('storage disabled')
    },
  })
  try {
    applyTheme('light')
    assert.equal(dom.documentElement.dataset.theme, 'light')
  } finally {
    restoreDoc()
    restoreStorage()
  }
})
