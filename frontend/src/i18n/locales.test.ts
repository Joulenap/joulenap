import assert from 'node:assert/strict'
import { test } from 'node:test'
import en from './en.json' with { type: 'json' }
import it from './it.json' with { type: 'json' }

type Pack = { [k: string]: string | Pack }

function flatten(pack: Pack, prefix = '', out: Record<string, string> = {}) {
  for (const [k, v] of Object.entries(pack)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out[key] = v
    else flatten(v, key, out)
  }
  return out
}

const EN = flatten(en as Pack)
const IT = flatten(it as Pack)

// A key present in only one pack does not fail the build, and i18next silently falls back
// to English mid-sentence — so this is the only thing that catches it.
test('en and it hold exactly the same keys', () => {
  assert.deepEqual(Object.keys(IT).sort(), Object.keys(EN).sort())
})

test('no translation is empty', () => {
  for (const [pack, name] of [
    [EN, 'en'],
    [IT, 'it'],
  ] as const)
    for (const [key, value] of Object.entries(pack))
      assert.ok(value.trim() !== '', `${name}.json: ${key} is empty`)
})

// i18next only pluralises on a variable literally named `count`, and only when both the
// _one and _other siblings exist. Half a plural renders the wrong form with no error.
test('plural keys come in complete sets and interpolate {{count}}', () => {
  const bases = new Set(
    Object.keys(EN)
      .filter((k) => /_(one|other)$/.test(k))
      .map((k) => k.replace(/_(one|other)$/, '')),
  )
  for (const base of bases)
    for (const pack of [EN, IT])
      for (const suffix of ['_one', '_other']) {
        const key = base + suffix
        assert.ok(key in pack, `missing plural sibling ${key}`)
      }
  // The _one form may spell the number out ("used by 1 route"); _other cannot.
  for (const base of bases)
    for (const pack of [EN, IT])
      assert.match(pack[`${base}_other`], /\{\{count\}\}/, `${base}_other must interpolate {{count}}`)
})
