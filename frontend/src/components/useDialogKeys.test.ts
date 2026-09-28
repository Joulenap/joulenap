import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isTopDialog, popDialog, pushDialog } from './useDialogKeys.ts'

test('only the dialog opened last answers the keyboard', () => {
  // A confirm opened over the route editor: Escape must close the confirm alone, where both
  // used to close because every open dialog listened for it.
  const editor = pushDialog()
  const confirm = pushDialog()
  assert.equal(isTopDialog(confirm), true)
  assert.equal(isTopDialog(editor), false)

  popDialog(confirm)
  assert.equal(isTopDialog(editor), true)
  popDialog(editor)
})

test('a dialog closed out of order leaves the right one on top', () => {
  const a = pushDialog()
  const b = pushDialog()
  popDialog(a)
  assert.equal(isTopDialog(b), true)
  popDialog(b)
  popDialog(b) // closing twice is harmless
  assert.equal(isTopDialog(b), false)
})
