import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError } from '../api/client.ts'
import type { GuestInfo, PbsDevice, PveDevice, Route } from '../api/types.ts'
import type { PveGuests } from './guestPanel.ts'
import {
  SETTINGS_TABS,
  deviceSaveErrors,
  deviceState,
  guardRoutes,
  normalizeTab,
  pbsCardMeta,
  pveCardMeta,
  routesUsing,
  validateDevice,
} from './deviceForm.ts'

const pve = (id: string, over: Partial<PveDevice> = {}): PveDevice => ({
  id,
  host: '192.168.1.10',
  port: 8006,
  verify_tls: false,
  api_token_id: 'joulenap@pve!routes',
  api_token_secret: '***REDACTED***',
  storages: { 'pbs-01': 'pbs-backup' },
  ...over,
})

const pbs = (id: string, over: Partial<PbsDevice> = {}): PbsDevice => ({
  id,
  host: '192.168.1.50',
  port: 8007,
  datastore: 'backup',
  fingerprint: '',
  api_token_id: 'joulenap@pbs!token',
  api_token_secret: '***REDACTED***',
  managed_power: true,
  mac: 'AA:BB:CC:DD:EE:FF',
  wol_broadcast_iface: '',
  wait_timeout: 180,
  wol_retries: 2,
  poweroff_task_wait: 600,
  ssh_user: 'root',
  ssh_key_path: '/app/data/id_ed25519',
  external: { first_task_wait: 900, idle_wait: 300 },
  ...over,
})

const route = (id: string, over: Partial<Route> = {}): Route => ({
  id,
  name: id,
  color: '#e8830f',
  enabled: true,
  notify: true,
  kind: 'backup',
  sources: [{ pve: 'pve-alpha', guests: { mode: 'all', list: [] } }],
  source_pbs: '',
  target: 'pbs-01',
  schedule: { time: '02:00', days: [true, true, true, true, true, true, true], cron: '' },
  retention: { keep_last: 0, keep_daily: 7, keep_weekly: 4, keep_monthly: 6, keep_yearly: 0 },
  sync_direction: 'pull',
  options: {
    mode: 'snapshot',
    bwlimit: 0,
    min_free_percent: 10,
    gc: true,
    verify_after: false,
    reverify_days: 30,
  },
  ...over,
})

const guest = (vmid: number, node: string): GuestInfo => ({
  vmid,
  name: `guest-${vmid}`,
  type: 'lxc',
  status: 'running',
  node,
  last_backup: null,
  pbs_ids: [],
})

// --- tabs ---------------------------------------------------------------------

test('the five tabs are in the mockup order', () => {
  assert.deepEqual([...SETTINGS_TABS], [
    'devices',
    'account',
    'notifications',
    'integrations',
    'advanced',
  ])
})

test('a tab id that no longer exists falls back to Devices', () => {
  assert.equal(normalizeTab('notifications'), 'notifications')
  // 'localization' and 'setup' were real tabs in 0.9 and are what a stale AppShell state or
  // an old gear handler would still hand over.
  assert.equal(normalizeTab('localization'), 'devices')
  assert.equal(normalizeTab('setup'), 'devices')
  assert.equal(normalizeTab(undefined), 'devices')
  assert.equal(normalizeTab(null), 'devices')
})

// --- removal guard -------------------------------------------------------------

test('routesUsing finds a PBS as target and as sync source', () => {
  const routes = [
    route('nightly', { target: 'pbs-01' }),
    route('offsite', { kind: 'sync', sources: [], source_pbs: 'pbs-01', target: 'pbs-02' }),
    route('lab', { target: 'pbs-01', name: '' }),
  ]
  // Both directions count: removing pbs-01 breaks the two routes writing to it and the sync
  // reading from it. A nameless route falls back to its id, as the backend does.
  assert.deepEqual(routesUsing(routes, 'pbss', 'pbs-01'), ['nightly', 'offsite', 'lab'])
  assert.deepEqual(routesUsing(routes, 'pbss', 'pbs-02'), ['offsite'])
  assert.deepEqual(routesUsing(routes, 'pbss', 'pbs-99'), [])
})

test('routesUsing finds a PVE through any of a route’s sources', () => {
  const routes = [
    route('nightly', {
      sources: [
        { pve: 'pve-alpha', guests: { mode: 'all', list: [] } },
        { pve: 'pve-beta', guests: { mode: 'all', list: [] } },
      ],
    }),
    route('offsite', { kind: 'sync', sources: [], source_pbs: 'pbs-01' }),
  ]
  assert.deepEqual(routesUsing(routes, 'pves', 'pve-beta'), ['nightly'])
  // A sync route has no PVE sources at all, so it must never block a PVE removal.
  assert.deepEqual(routesUsing(routes, 'pves', 'pbs-01'), [])
})

test('guardRoutes reads the 409 payload and survives a plain-string detail', () => {
  const conflict = new ApiError(409, "'pbs-01' is used by 2 route(s).", {
    message: "'pbs-01' is used by 2 route(s).",
    routes: ['Nightly', 'Lab'],
  })
  assert.deepEqual(guardRoutes(conflict), ['Nightly', 'Lab'])
  // Any other error carries no `raw`, and the caller falls back to err.message.
  assert.deepEqual(guardRoutes(new ApiError(502, 'boom')), [])
})

// --- card meta ------------------------------------------------------------------

test('a PVE is a cluster only when its guests span more than one node', () => {
  const spread: PveGuests = {
    pve: 'pve-alpha',
    guests: [guest(100, 'alpha-1'), guest(101, 'alpha-2'), guest(102, 'alpha-1')],
  }
  const single: PveGuests = { pve: 'pve-beta', guests: [guest(200, 'beta'), guest(201, 'beta')] }

  assert.deepEqual(pveCardMeta(pve('pve-alpha'), spread), {
    badge: 'cluster',
    host: '192.168.1.10:8006',
    nodes: 2,
    guests: 3,
    unknown: false,
  })
  assert.equal(pveCardMeta(pve('pve-beta'), single).badge, 'standalone')
  assert.equal(pveCardMeta(pve('pve-beta'), single).nodes, 1)
})

test('an unlisted or unreachable PVE reports unknown, not "standalone, 0 guests"', () => {
  // Before the first fan-out lands there is no group at all.
  assert.equal(pveCardMeta(pve('pve-lab')).unknown, true)
  const failed: PveGuests = { pve: 'pve-lab', guests: [], error: true }
  const meta = pveCardMeta(pve('pve-lab'), failed)
  assert.equal(meta.unknown, true)
  assert.equal(meta.guests, 0)
})

test('the PBS card shows the MAC only when Joulenap manages the power', () => {
  const routes = [route('nightly', { target: 'pbs-01' })]
  assert.deepEqual(pbsCardMeta(pbs('pbs-01'), routes), {
    badge: 'wol',
    host: '192.168.1.50:8007 · MAC AA:BB:CC:DD:EE:FF',
    datastore: 'backup',
    routes: 1,
  })
  // An always-on box keeps whatever MAC is on file but never wakes by it, so showing it
  // would advertise a capability that is switched off.
  const alwaysOn = pbsCardMeta(pbs('pbs-01', { managed_power: false }), routes)
  assert.equal(alwaysOn.badge, 'alwaysOn')
  assert.equal(alwaysOn.host, '192.168.1.50:8007')
})

test('an unreachable always-on PBS reads offline, never "always on"', () => {
  assert.equal(deviceState(true, true), 'connected')
  assert.equal(deviceState(false, true), 'sleeping')
  assert.equal(deviceState(false, false), 'offline')
  // No entry in /status yet (a device saved a moment ago) is not "connected".
  assert.equal(deviceState(undefined, true), 'sleeping')
})

// --- validation ------------------------------------------------------------------

test('managed power demands the fields that make it work', () => {
  const fields = (d: PbsDevice) => validateDevice(d).map((e) => e.field)

  assert.deepEqual(fields(pbs('pbs-01')), [])
  assert.deepEqual(fields(pbs('pbs-01', { mac: '', ssh_key_path: '' })), ['mac', 'ssh_key_path'])
  // Turning managed power off is exactly how you make those fields irrelevant.
  assert.deepEqual(fields(pbs('pbs-01', { mac: '', managed_power: false })), [])
  // Same escape hatch as the pydantic validator: a device with no host yet stays saveable.
  assert.deepEqual(fields(pbs('pbs-01', { host: '', mac: '' })), ['host'])
})

test('validateDevice checks the shared fields for both kinds', () => {
  assert.deepEqual(validateDevice(pve('pve-alpha')), [])
  assert.deepEqual(
    validateDevice(pve('pve-alpha', { port: 0 })).map((e) => e.field),
    ['port'],
  )
  assert.deepEqual(
    validateDevice(pve('pve-alpha', { port: 70000 })).map((e) => e.field),
    ['port'],
  )
  assert.deepEqual(
    validateDevice(pbs('pbs-01', { datastore: '' })).map((e) => e.field),
    ['datastore'],
  )
})

test('deviceSaveErrors pins a 422 on its field and a whole-config error on the banner', () => {
  const pinned = new ApiError(422, '[...]', [
    { loc: ['pbss', 0, 'port'], msg: 'Input should be less than or equal to 65535' },
  ])
  assert.deepEqual(deviceSaveErrors(pinned), [
    { field: 'port', message: 'Input should be less than or equal to 65535' },
  ])

  // A model_validator on the device or on Config names no field; `Value error, ` is
  // pydantic's own prefix and is noise to the reader.
  const banner = new ApiError(422, '[...]', [
    { loc: ['pbss', 0], msg: "Value error, pbs 'pbs-01': managed_power is on" },
  ])
  assert.deepEqual(deviceSaveErrors(banner), [
    { field: undefined, message: "pbs 'pbs-01': managed_power is on" },
  ])

  // The redaction guard answers a plain-string detail, so there is no list to unpack.
  assert.deepEqual(deviceSaveErrors(new ApiError(422, 'api_token_secret was sent as ***REDACTED***')), [
    { message: 'api_token_secret was sent as ***REDACTED***' },
  ])
})
