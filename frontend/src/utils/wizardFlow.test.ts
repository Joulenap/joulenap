import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { PbsDevice, PveDevice, WizardStorage } from '../api/types.ts'
import {
  FLOW_STEPS,
  defaultDeviceId,
  freshPbsDraft,
  freshPveDraft,
  isOrphanPbs,
  linkedStorages,
  matchStorage,
  newStorages,
  nextLabelKey,
  nextStep,
  pbsDeviceFrom,
  pveDeviceFrom,
  slugifyId,
  validateConnectStep,
  validateDeviceId,
  validateFinalDevice,
} from './wizardFlow.ts'

const pbs = (id: string, over: Partial<PbsDevice> = {}): PbsDevice => ({
  id,
  host: '192.168.1.50',
  port: 8007,
  datastore: 'backup',
  fingerprint: '',
  api_token_id: 'joulenap@pbs!token',
  api_token_secret: 'secret',
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

const pve = (id: string, storages: Record<string, string> = {}): PveDevice => ({
  id,
  host: '192.168.1.10',
  port: 8006,
  verify_tls: false,
  api_token_id: 'joulenap@pve!routes',
  api_token_secret: 'secret',
  storages,
})

const storage = (over: Partial<WizardStorage> = {}): WizardStorage => ({
  storage: 'pbs-backup',
  host: '192.168.1.50',
  port: 8007,
  datastore: 'backup',
  fingerprint: 'aa:bb',
  ...over,
})

// --- step navigation ------------------------------------------------------------

test('both flows have four steps, in the mockup order', () => {
  assert.deepEqual(FLOW_STEPS.pve, ['connection', 'discovery', 'configurePbs', 'finish'])
  assert.deepEqual(FLOW_STEPS.pbs, ['connection', 'wake', 'power', 'verification'])
})

test('a flow walks its steps in order and reports the end as -1', () => {
  assert.equal(nextStep('pve', 0, 1), 1)
  assert.equal(nextStep('pve', 1, 1), 2)
  assert.equal(nextStep('pve', 2, 1), 3)
  assert.equal(nextStep('pve', 3, 1), -1)
  assert.equal(nextStep('pve', 0, -1), 0) // Back on the first step stays put
})

test('flow A hops over configure-PBS in BOTH directions when nothing is being configured', () => {
  const rules = { noPbsToConfigure: true }
  assert.equal(nextStep('pve', 1, 1, rules), 3)
  // Back from Finish must land where Next came from, not on a step the user never saw.
  assert.equal(nextStep('pve', 3, -1, rules), 1)
})

test('flow A shows configure-PBS when there is one to configure', () => {
  assert.equal(nextStep('pve', 1, 1, { noPbsToConfigure: false }), 2)
  assert.equal(nextStep('pve', 3, -1, { noPbsToConfigure: false }), 2)
})

test('flow B skips wake-up and power-off for an unmanaged PBS, both ways', () => {
  const rules = { unmanagedPbs: true }
  assert.equal(nextStep('pbs', 0, 1, rules), 3)
  assert.equal(nextStep('pbs', 3, -1, rules), 0)
})

test('flow B keeps wake-up and power-off when Joulenap manages the power', () => {
  assert.equal(nextStep('pbs', 0, 1, { unmanagedPbs: false }), 1)
  assert.equal(nextStep('pbs', 2, 1, { unmanagedPbs: false }), 3)
})

test('the footer connects first, finishes last, and says Next in between', () => {
  assert.equal(nextLabelKey('pve', 0), 'wizard.nav.connect')
  assert.equal(nextLabelKey('pve', 1), 'wizard.nav.next')
  assert.equal(nextLabelKey('pve', 3), 'wizard.nav.finish')
  assert.equal(nextLabelKey('pbs', 3), 'wizard.nav.finish')
})

// --- device ids -------------------------------------------------------------------

test('slugify keeps what the id pattern accepts and drops the rest', () => {
  assert.equal(slugifyId('PBS Backup'), 'pbs-backup')
  assert.equal(slugifyId('pbs_01'), 'pbs_01') // `.` `_` `-` are all in the pattern
  assert.equal(slugifyId('pbs 01/x'), 'pbs-01-x')
  assert.equal(slugifyId('-leading'), 'leading')
  assert.equal(slugifyId('trailing-'), 'trailing')
  assert.equal(slugifyId('!!!'), '') // nothing usable survives
})

test('the default id falls back to the flow name and steps past collisions', () => {
  assert.equal(defaultDeviceId('pve', []), 'pve')
  assert.equal(defaultDeviceId('pve', ['pve']), 'pve-2')
  assert.equal(defaultDeviceId('pve', ['pve', 'pve-2']), 'pve-3')
})

test('a discovered PBS is proposed under the PVE storage name the user already knows', () => {
  assert.equal(defaultDeviceId('pbs', [], 'pbs-backup'), 'pbs-backup')
  assert.equal(defaultDeviceId('pbs', ['pbs-backup'], 'pbs-backup'), 'pbs-backup-2')
  // An unusable hint must not produce an id the backend would reject.
  assert.equal(defaultDeviceId('pbs', [], '###'), 'pbs')
})

test('id validation covers the gap the edit modal cannot reach', () => {
  assert.equal(validateDeviceId('pbs-01', []), null)
  assert.equal(validateDeviceId('  ', [])?.key, 'wizard.err.idRequired')
  assert.equal(validateDeviceId('-nope', [])?.key, 'wizard.err.idPattern')
  assert.equal(validateDeviceId('Nope', [])?.key, 'wizard.err.idPattern')
  const taken = validateDeviceId('pbs-01', ['pbs-01'])
  assert.equal(taken?.key, 'wizard.err.idTaken')
  assert.deepEqual(taken?.params, { id: 'pbs-01' })
})

// --- PBS discovery ----------------------------------------------------------------

test('a storage matches a registered PBS on host and datastore, not on its own id', () => {
  const registered = [pbs('pbs-01')]
  // Same box, a completely different storage name in this PVE.
  assert.equal(matchStorage(storage({ storage: 'whatever' }), registered)?.id, 'pbs-01')
  assert.equal(matchStorage(storage({ datastore: 'offsite' }), registered), null)
  assert.equal(matchStorage(storage({ host: '10.0.0.9' }), registered), null)
})

test('host matching ignores case and surrounding whitespace', () => {
  assert.equal(matchStorage(storage({ host: ' 192.168.1.50 ' }), [pbs('pbs-01')])?.id, 'pbs-01')
  assert.equal(matchStorage(storage({ host: 'PBS.LAN' }), [pbs('x', { host: 'pbs.lan' })])?.id, 'x')
})

test('already-registered storages become the PVE storages map, new ones do not', () => {
  const storages = [storage(), storage({ storage: 'offsite', datastore: 'offsite' })]
  const registered = [pbs('pbs-01')]
  assert.deepEqual(linkedStorages(storages, registered), { 'pbs-01': 'pbs-backup' })
  assert.deepEqual(
    newStorages(storages, registered).map((s) => s.storage),
    ['offsite'],
  )
})

test('a PBS no registered PVE maps is an orphan', () => {
  assert.equal(isOrphanPbs('pbs-02', [pve('pve-alpha', { 'pbs-01': 'pbs-backup' })]), true)
  assert.equal(isOrphanPbs('pbs-01', [pve('pve-alpha', { 'pbs-01': 'pbs-backup' })]), false)
  assert.equal(isOrphanPbs('pbs-01', []), true)
})

// --- assembling the POST bodies ----------------------------------------------------

test('the PVE body carries the minted token and the merged storages map', () => {
  const draft = { ...freshPveDraft([]), host: ' 192.168.1.10 ', port: 8006 }
  const device = pveDeviceFrom(draft, { id: 'root@pam!joulenap', secret: 'sec' }, { 'pbs-01': 'st' })
  assert.equal(device.host, '192.168.1.10') // trimmed
  assert.equal(device.api_token_id, 'root@pam!joulenap')
  assert.equal(device.api_token_secret, 'sec')
  assert.deepEqual(device.storages, { 'pbs-01': 'st' })
})

test('an unmanaged PBS is saved with its power fields cleared, not stale', () => {
  const draft = {
    ...freshPbsDraft([]),
    host: 'pbs.lan',
    datastore: 'backup',
    mac: 'AA:BB:CC:DD:EE:FF',
    wolIface: 'eth0',
    sshKeyPath: '/app/data/id_ed25519',
    managedPower: false,
  }
  const device = pbsDeviceFrom(draft, { id: 'tok', secret: 'sec' })
  assert.equal(device.managed_power, false)
  assert.equal(device.mac, '')
  assert.equal(device.wol_broadcast_iface, '')
  assert.equal(device.ssh_key_path, '')
  // ...and it must pass the backend's managed-power validator, which is the point.
  assert.deepEqual(validateFinalDevice(device, []), [])
})

test('a managed PBS keeps the wake and power-off fields', () => {
  const draft = {
    ...freshPbsDraft([]),
    host: 'pbs.lan',
    datastore: 'backup',
    mac: 'AA:BB:CC:DD:EE:FF',
    sshKeyPath: '/app/data/id_ed25519',
  }
  const device = pbsDeviceFrom(draft, { id: 'tok', secret: 'sec' })
  assert.equal(device.mac, 'AA:BB:CC:DD:EE:FF')
  assert.equal(device.ssh_key_path, '/app/data/id_ed25519')
  assert.deepEqual(validateFinalDevice(device, []), [])
})

test('a managed PBS with no MAC is refused before the POST', () => {
  const draft = { ...freshPbsDraft([]), host: 'pbs.lan', datastore: 'backup' }
  const errors = validateFinalDevice(pbsDeviceFrom(draft, { id: 't', secret: 's' }), [])
  assert.deepEqual(
    errors.map((e) => e.field),
    ['mac', 'ssh_key_path'],
  )
})

test('the final check still catches a duplicate id', () => {
  const draft = {
    ...freshPbsDraft([]),
    id: 'pbs-01',
    host: 'pbs.lan',
    datastore: 'backup',
    managedPower: false,
  }
  const errors = validateFinalDevice(pbsDeviceFrom(draft, { id: 't', secret: 's' }), ['pbs-01'])
  assert.equal(errors[0].key, 'wizard.err.idTaken')
})

// --- the connection step -----------------------------------------------------------

test('the connect step asks only for what it needs to make the call', () => {
  // No token and no MAC yet — root mode mints one, step 2 detects the other. A form that is
  // proceeding exactly as designed must not be blocked.
  const draft = { ...freshPveDraft([]), host: '192.168.1.10', password: 'pw' }
  assert.deepEqual(validateConnectStep(draft, []), [])
})

test('root mode needs a password, token mode needs both halves of the token', () => {
  const base = { ...freshPveDraft([]), host: 'h' }
  assert.equal(validateConnectStep(base, [])[0].field, 'password')
  const tok = { ...base, cred: 'token' as const }
  assert.equal(validateConnectStep(tok, [])[0].field, 'tokenId')
  assert.deepEqual(validateConnectStep({ ...tok, tokenId: 'a', tokenSecret: 'b' }, []), [])
})

test('a PBS connect step also requires the datastore, a PVE one does not', () => {
  const pbsDraft = { ...freshPbsDraft([]), host: 'h', password: 'pw' }
  assert.equal(
    validateConnectStep(pbsDraft, []).some((e) => e.field === 'datastore'),
    true,
  )
  assert.deepEqual(validateConnectStep({ ...pbsDraft, datastore: 'backup' }, []), [])
})

test('the connect step refuses a duplicate or malformed id', () => {
  const draft = { ...freshPveDraft([]), id: 'pve-alpha', host: 'h', password: 'pw' }
  assert.equal(validateConnectStep(draft, ['pve-alpha'])[0].key, 'wizard.err.idTaken')
  assert.equal(validateConnectStep({ ...draft, id: 'Bad' }, [])[0].key, 'wizard.err.idPattern')
})
