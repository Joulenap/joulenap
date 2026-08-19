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
