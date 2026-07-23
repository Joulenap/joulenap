import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

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

test('every var(--jn-*) referenced in code is defined in the palette', () => {
  const dark = varsIn(darkBlock)
  for (const file of ['theme.ts', 'utils/status.ts']) {
    const src = readFileSync(new URL(`./${file}`, import.meta.url), 'utf8')
    for (const m of src.matchAll(/var\((--jn-[\w-]+)\)/g)) {
      assert.ok(dark.has(m[1]), `${file} references ${m[1]} which index.css does not define`)
    }
  }
})
