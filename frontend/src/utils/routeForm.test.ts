import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError } from '../api/client.ts'
import type { GuestInfo, PbsDevice, PveDevice, Route, RouteSource } from '../api/types.ts'
import type { PveGuests } from './guestPanel.ts'
import {
  DEFAULT_OPTIONS,
  DEFAULT_RETENTION,
  type RouteDraft,
  draftFromRoute,
  draftToRoute,
  guestTally,
  inferKind,
  isPicked,
  pveSources,
  retentionOverlaps,
  saveErrors,
  sectionsFor,
  showsGuestGroups,
  slugifyRouteId,
  toggleGuest,
  validateDraft,
} from './routeForm.ts'

const pve = (id: string, storages: Record<string, string> = { 'pbs-01': 'pbs-backup' }): PveDevice => ({
  id,
  host: '10.0.0.1',
  port: 8006,
  verify_tls: false,
  api_token_id: '',
  api_token_secret: '',
  storages,
})

const pbs = (id: string, over: Partial<PbsDevice> = {}): PbsDevice => ({
  id,
  host: '10.0.0.9',
  port: 8007,
  datastore: 'backup',
  fingerprint: '',
  api_token_id: '',
  api_token_secret: '',
  managed_power: true,
  mac: 'aa:bb:cc:dd:ee:ff',
  wol_broadcast_iface: '',
  wait_timeout: 180,
  wol_retries: 2,
  poweroff_task_wait: 600,
  ssh_user: 'root',
  ssh_key_path: '/app/data/id_ed25519',
  external: { first_task_wait: 900, idle_wait: 300 },
  ...over,
})

const src = (pveId: string, list?: number[]): RouteSource => ({
  pve: pveId,
  guests: list ? { mode: 'include', list } : { mode: 'all', list: [] },
})

const route = (id: string, over: Partial<Route> = {}): Route => ({
  id,
  name: id,
  color: '#e8830f',
  enabled: true,
  notify: true,
  kind: 'backup',
  sources: [src('pve-alpha')],
  source_pbs: '',
  target: 'pbs-01',
  schedule: { time: '02:00', days: Array(7).fill(true), cron: '' },
  retention: { ...DEFAULT_RETENTION },
  sync_direction: 'pull',
  options: { ...DEFAULT_OPTIONS },
  ...over,
})

const draft = (over: Partial<RouteDraft> = {}): RouteDraft => ({
  ...draftFromRoute(null, [pbs('pbs-01'), pbs('pbs-02')]),
  name: 'Nightly',
  sourceIds: ['pve:pve-alpha'],
  ...over,
})

const guest = (vmid: number): GuestInfo => ({
  vmid,
  name: `g${vmid}`,
  type: 'lxc',
  status: 'running',
  node: 'n1',
  last_backup: null,
  pbs_ids: [],
})

const groups = (map: Record<string, number[]>): PveGuests[] =>
  Object.entries(map).map(([p, vmids]) => ({ pve: p, guests: vmids.map(guest) }))

// --- kind inference ----------------------------------------------------------

test('PVE sources make a backup route', () => {
  assert.equal(inferKind(['pve:pve-alpha', 'pve:pve-beta'], 'external'), 'backup')
})

test('a PBS among the sources makes it a sync route, even mixed with a PVE', () => {
  assert.equal(inferKind(['pbs:pbs-01'], 'external'), 'sync')
  // Mixed is invalid, but it must still *read* as sync so the single-source error is what
  // the user is told — not a backup route silently trying to vzdump a PBS.
  assert.equal(inferKind(['pve:pve-alpha', 'pbs:pbs-01'], 'external'), 'sync')
})

test('no sources: the On-wake segmented is what picks the kind', () => {
  assert.equal(inferKind([], 'external'), 'external')
  assert.equal(inferKind([], 'verify'), 'verify')
})

test('each kind shows its own sections', () => {
  assert.deepEqual(sectionsFor('backup'), { onWake: false, guests: true, sync: false, retention: true })
  assert.deepEqual(sectionsFor('sync'), { onWake: false, guests: false, sync: true, retention: true })
  assert.deepEqual(sectionsFor('external'), { onWake: true, guests: false, sync: false, retention: false })
  assert.deepEqual(sectionsFor('verify'), { onWake: true, guests: false, sync: false, retention: false })
})

// --- guest selection ---------------------------------------------------------

test('a source that has never been narrowed covers every guest', () => {
  assert.equal(isPicked({}, 'pve-alpha', 100), true)
  const tally = guestTally(['pve:pve-alpha'], groups({ 'pve-alpha': [100, 101] }), {})
  assert.deepEqual(tally, { total: 2, chosen: 2 })
})

test('unchecking the first guest narrows the source to all the others', () => {
  const sel = toggleGuest({}, 'pve-alpha', 101, [100, 101, 102])
  assert.deepEqual(sel, { 'pve-alpha': [100, 102] })
  assert.equal(isPicked(sel, 'pve-alpha', 101), false)
  assert.equal(isPicked(sel, 'pve-alpha', 100), true)
})

test('re-checking a guest puts it back in vmid order', () => {
  const sel = toggleGuest({ 'pve-alpha': [100, 102] }, 'pve-alpha', 101, [100, 101, 102])
  assert.deepEqual(sel['pve-alpha'], [100, 101, 102])
})

test('the same vmid on two PVEs is two independent choices', () => {
  // The whole reason guests live per source: vmid 100 exists on both boxes.
  let sel = toggleGuest({}, 'pve-alpha', 100, [100, 101])
  sel = { ...sel }
  assert.equal(isPicked(sel, 'pve-alpha', 100), false)
  assert.equal(isPicked(sel, 'pve-beta', 100), true)
})

test('the counter spans every PVE source and ignores the PBS chips', () => {
  const g = groups({ 'pve-alpha': [100, 101], 'pve-beta': [200] })
  const tally = guestTally(['pve:pve-alpha', 'pve:pve-beta', 'pbs:pbs-01'], g, { 'pve-alpha': [100] })
  assert.deepEqual(tally, { total: 3, chosen: 2 })
})

test('a PVE whose listing has not arrived contributes nothing rather than a wrong zero', () => {
  assert.deepEqual(guestTally(['pve:pve-alpha'], [], {}), { total: 0, chosen: 0 })
})

test('per-source group headers appear only from the second PVE source on', () => {
  assert.equal(showsGuestGroups(['pve:pve-alpha']), false)
  assert.equal(showsGuestGroups(['pve:pve-alpha', 'pbs:pbs-01']), false)
  assert.equal(showsGuestGroups(['pve:pve-alpha', 'pve:pve-beta']), true)
})

// --- draft <-> Route ---------------------------------------------------------

test('a sync route loads its source_pbs into the same chip row as a PVE would', () => {
  const d = draftFromRoute(
    route('offsite', { kind: 'sync', sources: [], source_pbs: 'pbs-01', target: 'pbs-02' }),
    [],
  )
  assert.deepEqual(d.sourceIds, ['pbs:pbs-01'])
  assert.equal(d.target, 'pbs-02')
})

test('a PVE and a PBS sharing an id are two independent chips', () => {
  // Device ids are unique only within their own list, so `alpha` can be both. With bare ids
  // in the draft, selecting the PVE also lit the PBS chip, flipped the kind to Sync, dropped
  // the PVE source and saved `{kind: 'sync', source_pbs: 'alpha', sources: []}` — a route
  // that syncs a box to itself instead of backing up a hypervisor.
  const sources = ['pve:alpha']
  assert.equal(inferKind(sources, 'external'), 'backup')
  assert.deepEqual(pveSources(sources), ['alpha'])

  const saved = draftToRoute(draft({ sourceIds: sources, target: 'pbs-01' }), [])
  assert.equal(saved.kind, 'backup')
  assert.equal(saved.source_pbs, '')
  assert.deepEqual(
    saved.sources.map((s) => s.pve),
    ['alpha'],
  )
})

test('a verify route comes back with the On-wake segmented on Verify', () => {
  const d = draftFromRoute(route('vfy', { kind: 'verify', sources: [] }), [])
  assert.equal(d.onWake, 'verify')
  assert.deepEqual(d.sourceIds, [])
})

test('an all-guests route reads as All with no selection recorded', () => {
  const d = draftFromRoute(route('nightly'), [])
  assert.equal(d.guestMode, 'all')
  assert.deepEqual(d.selection, {})
})

test('an include-list route round-trips through the draft unchanged', () => {
  const original = route('lab', { sources: [src('pve-lab', [130, 131])] })
  const d = draftFromRoute(original, [])
  assert.equal(d.guestMode, 'include')
  assert.deepEqual(d.selection, { 'pve-lab': [130, 131] })
  assert.deepEqual(draftToRoute(d, []).sources, original.sources)
})

test('Selection mode leaves an untouched source on "all" instead of saving an empty list', () => {
  // Guest lists load asynchronously; a source the user never narrowed must not become
  // "back up nothing" just because its listing had not arrived.
  const d = draft({ guestMode: 'include', sourceIds: ['pve:pve-alpha', 'pve:pve-beta'], selection: { 'pve-beta': [200] } })
  assert.deepEqual(draftToRoute(d, []).sources, [
    { pve: 'pve-alpha', guests: { mode: 'all', list: [] } },
    { pve: 'pve-beta', guests: { mode: 'include', list: [200] } },
  ])
})

test('switching back to All drops the recorded lists', () => {
  const d = draft({ guestMode: 'all', selection: { 'pve-alpha': [100] } })
  assert.deepEqual(draftToRoute(d, []).sources, [
    { pve: 'pve-alpha', guests: { mode: 'all', list: [] } },
  ])
})

test('a sync draft saves source_pbs and no sources', () => {
  const saved = draftToRoute(draft({ sourceIds: ['pbs:pbs-01'], target: 'pbs-02' }), [])
  assert.equal(saved.kind, 'sync')
  assert.equal(saved.source_pbs, 'pbs-01')
  assert.deepEqual(saved.sources, [])
})

test('an external draft saves neither sources nor source_pbs', () => {
  const saved = draftToRoute(draft({ sourceIds: [], onWake: 'external' }), [])
  assert.equal(saved.kind, 'external')
  assert.deepEqual(saved.sources, [])
  assert.equal(saved.source_pbs, '')
})

test('a pinned cron survives an edit that never touches the schedule', () => {
  const d = draftFromRoute(route('legacy', { schedule: { time: '04:00', days: Array(7).fill(true), cron: '0 23 * * 0,1' } }), [])
  assert.equal(draftToRoute(d, []).schedule.cron, '0 23 * * 0,1')
})

test('an edit keeps its stored id, so run history stays attached', () => {
  const d = draft({ id: 'nightly', name: 'Renamed nightly' })
  assert.equal(draftToRoute(d, ['nightly']).id, 'nightly')
})

test('a new route derives its id from the name and dodges the ones in use', () => {
  assert.equal(slugifyRouteId('Weekend lab', []), 'weekend-lab')
  assert.equal(slugifyRouteId('Weekend lab', ['weekend-lab']), 'weekend-lab-2')
  assert.equal(slugifyRouteId('Weekend lab', ['weekend-lab', 'weekend-lab-2']), 'weekend-lab-3')
  // The backend's pattern demands a leading alphanumeric.
  assert.equal(slugifyRouteId('  ...Offsite!', []), 'offsite')
  assert.equal(slugifyRouteId('§§§', []), 'route')
})

// --- validation --------------------------------------------------------------

const PVES = [pve('pve-alpha'), pve('pve-beta'), pve('pve-lab', {})]
const PBSS = [pbs('pbs-01'), pbs('pbs-02')]

const keys = (d: RouteDraft, pves = PVES, pbss = PBSS) =>
  validateDraft(d, pves, pbss).map((e) => e.key)

test('a valid backup draft has nothing to report', () => {
  assert.deepEqual(keys(draft()), [])
})

test('the route name is required', () => {
  assert.ok(keys(draft({ name: '   ' })).includes('dashboard.routeModal.errName'))
})

test('a sync route refuses a second source', () => {
  assert.ok(
    keys(draft({ sourceIds: ['pbs:pbs-01', 'pbs:pbs-02'], target: 'pbs-01' })).includes(
      'dashboard.routeModal.errSyncSingle',
    ),
  )
})

test('a backup target unreachable from a source is caught before the 422', () => {
  // pve-lab has no storage mapping at all; the backend raises the same thing in
  // Config._check_references.
  const errors = validateDraft(draft({ sourceIds: ['pve-lab'] }), PVES, PBSS)
  const err = errors.find((e) => e.key === 'dashboard.routeModal.errNoStorage')
  assert.ok(err)
  assert.deepEqual(err.params, { pve: 'pve-lab', pbs: 'pbs-01' })
  assert.equal(err.field, 'sources')
})

test('a target the source does map is accepted', () => {
  assert.deepEqual(keys(draft({ sourceIds: ['pve:pve-alpha'], target: 'pbs-01' })), [])
  assert.ok(
    keys(draft({ sourceIds: ['pve:pve-alpha'], target: 'pbs-02' })).includes(
      'dashboard.routeModal.errNoStorage',
    ),
  )
})

test('an external route onto an always-on PBS is refused', () => {
  const pbss = [pbs('pbs-01'), pbs('pbs-02', { managed_power: false, mac: '' })]
  assert.ok(
    keys(draft({ sourceIds: [], onWake: 'external', target: 'pbs-02' }), PVES, pbss).includes(
      'dashboard.routeModal.errExternalUnmanaged',
    ),
  )
  // A Verify route on the same box is fine — it starts work of its own.
  assert.deepEqual(keys(draft({ sourceIds: [], onWake: 'verify', target: 'pbs-02' }), PVES, pbss), [])
})

test('a schedule with no day selected would never fire', () => {
  assert.ok(keys(draft({ days: Array(7).fill(false) })).includes('dashboard.routeModal.errNoDay'))
})

test('a cron-pinned route ignores the day check — its days are unused', () => {
  assert.deepEqual(keys(draft({ days: Array(7).fill(false), cron: '0 23 * * 1' })), [])
})

test('Selection narrowed down to nothing everywhere backs up nothing', () => {
  assert.ok(
    keys(draft({ guestMode: 'include', selection: { 'pve-alpha': [] } })).includes(
      'dashboard.routeModal.errNoGuests',
    ),
  )
  // One source still covering something is enough.
  assert.deepEqual(
    keys(
      draft({
        guestMode: 'include',
        sourceIds: ['pve:pve-alpha', 'pve:pve-beta'],
        selection: { 'pve-alpha': [], 'pve-beta': [200] },
      }),
    ),
    [],
  )
})

// --- retention overlap -------------------------------------------------------

test('two all-guest routes onto the same target with different retention warn about each other', () => {
  const other = route('weekly', {
    name: 'Weekly',
    retention: { ...DEFAULT_RETENTION, keep_daily: 30 },
  })
  assert.deepEqual(retentionOverlaps(draft({ id: 'nightly' }), [other]), ['Weekly'])
})

test('the same retention is not a conflict, however much they overlap', () => {
  assert.deepEqual(retentionOverlaps(draft({ id: 'nightly' }), [route('weekly')]), [])
})

test('different targets never prune each other', () => {
  const other = route('weekly', {
    target: 'pbs-02',
    retention: { ...DEFAULT_RETENTION, keep_daily: 30 },
  })
  assert.deepEqual(retentionOverlaps(draft({ id: 'nightly' }), [other]), [])
})

test('disjoint guest lists on one target are the legitimate split, not a warning', () => {
  const other = route('weekly', {
    sources: [src('pve-alpha', [200, 201])],
    retention: { ...DEFAULT_RETENTION, keep_daily: 30 },
  })
  const mine = draft({ id: 'nightly', guestMode: 'include', selection: { 'pve-alpha': [100, 101] } })
  assert.deepEqual(retentionOverlaps(mine, [other]), [])
  // One shared vmid is enough to make them fight.
  const overlapping = draft({ id: 'nightly', guestMode: 'include', selection: { 'pve-alpha': [101, 200] } })
  assert.deepEqual(retentionOverlaps(overlapping, [other]), ['weekly'])
})

test('an all-guests route overlaps every list on the same PVE', () => {
  const other = route('weekly', {
    sources: [src('pve-alpha', [999])],
    retention: { ...DEFAULT_RETENTION, keep_yearly: 2 },
  })
  assert.deepEqual(retentionOverlaps(draft({ id: 'nightly' }), [other]), ['weekly'])
})

test('a route never warns about itself while being edited', () => {
  const stored = route('nightly', { retention: { ...DEFAULT_RETENTION, keep_daily: 30 } })
  assert.deepEqual(retentionOverlaps(draft({ id: 'nightly' }), [stored]), [])
})

test('only backup routes prune, so a sync draft warns about nothing', () => {
  const other = route('weekly', { retention: { ...DEFAULT_RETENTION, keep_daily: 30 } })
  const sync = draft({ id: 'offsite', sourceIds: ['pbs:pbs-02'], target: 'pbs-01' })
  assert.deepEqual(retentionOverlaps(sync, [other]), [])
})

// --- server errors -----------------------------------------------------------

test('a 422 list is unpacked onto the fields it names', () => {
  const err = new ApiError(422, '[...]', [
    { type: 'string_too_short', loc: ['routes', 2, 'name'], msg: 'String should have at least 1 character' },
    { type: 'value_error', loc: ['routes', 2, 'schedule'], msg: 'Value error, schedule.days selects no day' },
  ])
  assert.deepEqual(saveErrors(err), [
    { field: 'name', message: 'String should have at least 1 character' },
    { field: 'days', message: 'schedule.days selects no day' },
  ])
})

test('a whole-config validator error has no field and becomes a banner', () => {
  const err = new ApiError(422, '[...]', [
    { type: 'value_error', loc: [], msg: "Value error, route 'x': pve 'y' has no storage mapping" },
  ])
  assert.deepEqual(saveErrors(err), [
    { field: undefined, message: "route 'x': pve 'y' has no storage mapping" },
  ])
})

test('a duplicate id is reported on the name, which is what the user typed', () => {
  assert.deepEqual(saveErrors(new ApiError(409, "Route 'lab' already exists")), [
    { field: 'name', key: 'dashboard.routeModal.errDuplicate' },
  ])
})

test('anything else falls back to the message the client already built', () => {
  assert.deepEqual(saveErrors(new ApiError(500, 'config.yaml is read-only')), [
    { message: 'config.yaml is read-only' },
  ])
})

// --- the defaults a new route starts from ------------------------------------
//
// Asserted whole, because these are what the user gets by opening the modal and pressing
// Save. Nothing pinned them, so every flag in DEFAULT_OPTIONS and every field of a fresh
// draft could flip without a single test noticing.

test('a new route runs GC, does not verify, and does not remove vanished snapshots', () => {
  assert.deepEqual(DEFAULT_OPTIONS, {
    mode: 'snapshot',
    bwlimit: 0,
    min_free_percent: 0, // the free-space preflight is opt-in
    gc: true, // reclaiming space is the point of an automated backup
    verify_after: false, // verification is slow; it is a choice, not a default
    reverify_days: 30,
    transfer_last: 0,
    remove_vanished: false, // never delete on the target unless asked
  })
})

test('the default retention is a week of dailies tapering to six months', () => {
  assert.deepEqual(DEFAULT_RETENTION, {
    keep_last: 0, // no "keep the last N whatever they are": the tiers below decide
    keep_daily: 7,
    keep_weekly: 4,
    keep_monthly: 6,
    keep_yearly: 0,
  })
})

test('a fresh draft is enabled, notifying, and armed every day', () => {
  const draft = draftFromRoute(null, [pbs('pbs-01'), pbs('pbs-02')])

  assert.equal(draft.enabled, true) // a route you just created should run
  assert.equal(draft.notify, true) // and tell you when it did
  assert.deepEqual(draft.days, Array(7).fill(true))
  assert.equal(draft.time, '04:00')
  assert.equal(draft.cron, '') // the simple schedule, not the raw escape hatch
  assert.equal(draft.guestMode, 'all')
  assert.equal(draft.target, 'pbs-01') // the first backup server, not an empty select
  assert.deepEqual(draft.options, DEFAULT_OPTIONS)
})

test('a fresh draft has no target when there is no backup server yet', () => {
  assert.equal(draftFromRoute(null, []).target, '')
})

test('a retention change in any single field counts as a change', () => {
  // The comparison is a chain of ors; with one link broken, editing that one field would
  // save a route whose retention silently reverted to the stored value.
  const stored = route('nightly')
  for (const field of ['keep_last', 'keep_daily', 'keep_weekly', 'keep_monthly', 'keep_yearly'] as const) {
    const draft = {
      ...draftFromRoute(stored, [pbs('pbs-01')]),
      retention: { ...DEFAULT_RETENTION, [field]: 99 },
    }
    const saved = draftToRoute(draft, [])
    assert.equal(saved.retention?.[field], 99, field)
  }
})

test('the sync single-source rule only applies to sync routes', () => {
  // `kind === 'sync' && length > 1`: with the kind check dropped, a two-PVE backup route
  // (the normal case) would be refused.
  const twoPves = draft({ sourceIds: ['pve:pve-alpha', 'pve:pve-beta'], target: 'pbs-01' })
  assert.ok(!keys(twoPves).includes('dashboard.routeModal.errSyncSingle'))
})

test('the storage check only applies once a target is chosen', () => {
  // `kind === 'backup' && draft.target`: without the target check it would report "no
  // storage for pbs undefined" the moment a source chip is added.
  const noTarget = draft({ sourceIds: ['pve-lab'], target: '' })
  assert.ok(!keys(noTarget).includes('dashboard.routeModal.errNoStorage'))
})

test('an external route onto a managed box is accepted', () => {
  // `target && !target.managed_power`: with the flag check dropped, every external route
  // would be refused, including the ones that are the whole point of the kind.
  const managed = draft({ sourceIds: [], onWake: 'external', target: 'pbs-01' })
  assert.ok(!keys(managed).includes('dashboard.routeModal.errExternalUnmanaged'))
})

test('a cron-pinned route does not need a weekday ticked', () => {
  // `!draft.cron && !days.some(...)`: the raw cron replaces the weekday toggles entirely,
  // so demanding a day as well would make the advanced schedule unusable.
  const pinned = draft({ sourceIds: ['pve:pve-alpha'], target: 'pbs-01' })
  pinned.cron = '0 4 1 * *'
  pinned.days = Array(7).fill(false)
  assert.ok(!keys(pinned).includes('dashboard.routeModal.errNoDay'))

  const noCronNoDays = draft({ sourceIds: ['pve:pve-alpha'], target: 'pbs-01' })
  noCronNoDays.days = Array(7).fill(false)
  assert.ok(keys(noCronNoDays).includes('dashboard.routeModal.errNoDay'))
})

test('a difference in any single retention field is a conflict', () => {
  // retentionDiffers is a chain of ors; a broken link means two routes that really will
  // prune each other's snapshots are reported as compatible, which is the silent
  // data-loss case this warning exists for.
  for (const field of ['keep_last', 'keep_daily', 'keep_weekly', 'keep_monthly', 'keep_yearly'] as const) {
    const other = route('weekly', {
      name: 'Weekly',
      retention: { ...DEFAULT_RETENTION, [field]: 42 },
    })
    assert.deepEqual(
      retentionOverlaps(draft({ id: 'nightly' }), [other]),
      ['Weekly'],
      `${field} differing should be a conflict`,
    )
  }
})

test('the guest-selection check only applies to backup routes', () => {
  // Reachable the moment a PBS chip is added to a route that already has PVE sources:
  // inferKind calls that sync, but pveSources is still non-empty, so without the kind
  // check the form would refuse to save over guests the route no longer backs up.
  const mixed = draft({
    sourceIds: ['pve:pve-alpha', 'pbs:pbs-01'],
    target: 'pbs-02',
    guestMode: 'include',
    selection: { 'pve-alpha': [] },
  })
  assert.equal(inferKind(mixed.sourceIds, mixed.onWake), 'sync')
  assert.ok(!keys(mixed).includes('dashboard.routeModal.errNoGuests'))

  // The same empty selection on a real backup route is still refused.
  const backup = draft({
    sourceIds: ['pve:pve-alpha'],
    target: 'pbs-01',
    guestMode: 'include',
    selection: { 'pve-alpha': [] },
  })
  assert.ok(keys(backup).includes('dashboard.routeModal.errNoGuests'))
})
