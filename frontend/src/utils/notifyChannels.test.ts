import assert from 'node:assert/strict'
import { test } from 'node:test'
import { channelLabel } from './notifyChannels.ts'

const t = (key: string) => `T(${key})`

test('known channels resolve to their i18n title', () => {
  assert.equal(channelLabel('telegram', t), 'T(settings.notifications.telegramTitle)')
  assert.equal(channelLabel('ntfy', t), 'T(settings.notifications.ntfyTitle)')
  assert.equal(channelLabel('email', t), 'T(settings.notifications.emailTitle)')
  assert.equal(channelLabel('discord', t), 'T(settings.notifications.discordTitle)')
})

test('custom channels are shown verbatim, not run through i18n', () => {
  assert.equal(channelLabel('custom #1', t), 'custom #1')
  assert.equal(channelLabel('custom #12', t), 'custom #12')
})
