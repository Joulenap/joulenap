import assert from 'node:assert/strict'
import { test } from 'node:test'
import { guestsSelectionError } from './guests.ts'

test('guestsSelectionError blocks Selective mode with no guests', () => {
  assert.equal(guestsSelectionError('selective', 0), 'dashboard.noGuestsSelected')
})

test('guestsSelectionError allows Selective mode with at least one guest', () => {
  assert.equal(guestsSelectionError('selective', 1), null)
  assert.equal(guestsSelectionError('selective', 3), null)
})

test('guestsSelectionError never blocks General or Exclude mode (empty is valid)', () => {
  assert.equal(guestsSelectionError('general', 0), null)
  assert.equal(guestsSelectionError('exclude', 0), null)
})
