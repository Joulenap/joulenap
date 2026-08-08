import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { GuestInfo } from '../api/types.ts'
import { countNeverBacked, groupGuests, guestTypeLabel, pbsChip } from './guestPanel.ts'

const guest = (vmid: number, name: string, type = 'qemu'): GuestInfo => ({
  vmid,
  name,
  type,
  status: 'running',
  node: 'n1',
  last_backup: null,
  pbs_ids: [],
})

const GROUPS = [
  { pve: 'pve-alpha', guests: [guest(102, 'nextcloud', 'lxc'), guest(100, 'web-proxy')] },
  { pve: 'pve-lab', guests: [guest(300, 'k3s-master')] },
]

test('guests are ordered by vmid inside their group, not by input order', () => {
  const out = groupGuests(GROUPS, '')
  assert.deepEqual(out[0].guests.map((g) => g.vmid), [100, 102])
})

test('grouping does not mutate the caller\'s arrays', () => {
  const input = [{ pve: 'a', guests: [guest(2, 'b'), guest(1, 'a')] }]
  groupGuests(input, '')
  assert.deepEqual(input[0].guests.map((g) => g.vmid), [2, 1])
})

test('a query that empties a group removes the group, sticky divider included', () => {
  const out = groupGuests(GROUPS, 'k3s')
  assert.deepEqual(out.map((g) => g.pve), ['pve-lab'])
})

test('search matches name, vmid and the CT/VM badge', () => {
  assert.equal(groupGuests(GROUPS, 'cloud')[0].guests[0].vmid, 102)
  assert.equal(groupGuests(GROUPS, '300')[0].pve, 'pve-lab')
  // "CT" is what the row shows; the API says "lxc", which nobody would type.
  assert.deepEqual(
    groupGuests(GROUPS, 'ct').flatMap((g) => g.guests.map((x) => x.vmid)),
    [102],
  )
})

test('search is case-insensitive and ignores surrounding spaces', () => {
  assert.equal(groupGuests(GROUPS, '  NEXTcloud ')[0].guests[0].vmid, 102)
})

test('a query matching nothing yields no groups at all', () => {
  assert.deepEqual(groupGuests(GROUPS, 'zzz'), [])
})

test('an unreachable PVE keeps its group under every query', () => {
  // Hiding it would turn "could not reach this PVE" into "this PVE has no guests".
  const groups = [...GROUPS, { pve: 'pve-beta', guests: [], error: true }]
  assert.ok(groupGuests(groups, 'zzz').some((g) => g.pve === 'pve-beta'))
  assert.ok(groupGuests(groups, '').some((g) => g.pve === 'pve-beta'))
})

test('type labels map to the badge text', () => {
  assert.equal(guestTypeLabel('qemu'), 'VM')
  assert.equal(guestTypeLabel('lxc'), 'CT')
  assert.equal(guestTypeLabel('weird'), 'WEIRD')
})

test('one copy shows the bare id, several show an overflow count', () => {
  assert.equal(pbsChip(['pbs-01'])?.label, 'pbs-01')
  assert.equal(pbsChip(['pbs-01'])?.many, false)
  const two = pbsChip(['pbs-01', 'pbs-02'])
  assert.equal(two?.label, 'pbs-01 +1')
  assert.equal(two?.many, true)
  assert.equal(two?.title, 'pbs-01, pbs-02')
})

test('a guest that has never been backed up gets no chip', () => {
  assert.equal(pbsChip([]), null)
})

test('countNeverBacked counts unprotected guests, skipping unreachable PVEs', () => {
  const groups = [
    {
      pve: 'pve-alpha',
      guests: [
        guest(100, 'web-proxy'),
        { ...guest(101, 'db'), last_backup: '2026-08-07T02:00:00Z' },
      ],
    },
    // Unknown is not unprotected: an unreachable PVE must not inflate the warning.
    { pve: 'pve-beta', guests: [guest(200, 'ghost')], error: true },
  ]
  assert.equal(countNeverBacked(groups), 1)
  assert.equal(countNeverBacked([]), 0)
})
