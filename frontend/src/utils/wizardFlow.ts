// The device wizard's whole rule set, as pure functions.
//
// The test harness is `node --test` with no DOM, so nothing inside a component can be covered
// (same constraint as utils/routeForm.ts and utils/deviceForm.ts). Which step comes next, what
// a new device is called, which discovered storage is already registered, what gets POSTed and
// whether the result is an orphan all live here; the two flow components only render answers.

import type { PbsDevice, PveDevice, WizardStorage } from '../api/types.ts'
import { type DeviceError, validateDevice } from './deviceForm.ts'

export type Flow = 'pve' | 'pbs'

/** i18n key suffixes for the stepper, in the order the mockup draws them. */
export const FLOW_STEPS: Record<Flow, readonly string[]> = {
  pve: ['connection', 'discovery', 'configurePbs', 'finish'],
  pbs: ['connection', 'wake', 'power', 'verification'],
} as const

/** Which credentials a step collects. Root is transient — used once to provision, never sent
 *  to the device create — so the two field rows are mutually exclusive, not merged. */
export type CredMode = 'root' | 'token'

// --- step navigation ------------------------------------------------------------

export interface SkipRules {
  /** Flow A: no new PBS is being configured, so step 3 does not apply. */
  noPbsToConfigure?: boolean
  /** Flow B: Joulenap does not manage this box's power, so wake-up and power-off do not apply. */
  unmanagedPbs?: boolean
}

/**
 * The next step in `dir` (+1/-1), hopping over the steps that do not apply.
 *
 * Mirrors `design/overhaul/src/wiz2.html`'s `advance()`: the skips work in *both* directions,
 * so Back from Finish lands where Next came from rather than on a step the user never saw.
 * Returns `-1` past the last step, which the caller reads as "the flow is done".
 */
export function nextStep(flow: Flow, current: number, dir: 1 | -1, rules: SkipRules = {}): number {
  const last = FLOW_STEPS[flow].length - 1
  let step = current + dir
  if (flow === 'pve' && step === 2 && rules.noPbsToConfigure) step += dir
  if (flow === 'pbs' && rules.unmanagedPbs && (step === 1 || step === 2)) {
    step = dir > 0 ? 3 : 0
  }
  if (step < 0) return 0
  return step > last ? -1 : step
}

/** The footer button's label key: the first step connects, the last one finishes. */
export function nextLabelKey(flow: Flow, step: number): string {
  if (step === 0) return 'wizard.nav.connect'
  return step === FLOW_STEPS[flow].length - 1 ? 'wizard.nav.finish' : 'wizard.nav.next'
}

// --- device ids -------------------------------------------------------------------

/** `config.py`'s `PveDevice.id` / `PbsDevice.id` pattern, verbatim. */
const ID_PATTERN = /^[a-z0-9][a-z0-9._-]*$/

/**
 * Slugify `hint` into something the id pattern accepts, or `""` if nothing usable survives.
 *
 * Used on the PVE storage id when a PBS is discovered from a PVE's storage config — which is
 * what the mockup's "pbs-01" reads as — so the proposed name is one the user already recognises
 * from their own PVE.
 */
export function slugifyId(hint: string): string {
  const slug = hint
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .replace(/-+$/, '')
  return ID_PATTERN.test(slug) ? slug : ''
}

/**
 * A free id to pre-fill the Name field with.
 *
 * The mockup has no id field at all, but the id is required, unique, pattern-matched and is the
 * name the whole UI displays (topology, route editor, history) — so the wizard proposes one and
 * lets the user change it, rather than inventing a name behind their back.
 */
export function defaultDeviceId(flow: Flow, existing: string[], hint = ''): string {
  const taken = new Set(existing)
  const base = slugifyId(hint) || flow
  if (!taken.has(base)) return base
  for (let n = 2; ; n++) {
    const candidate = `${base}-${n}`
    if (!taken.has(candidate)) return candidate
  }
}

/**
 * What the backend would reject about an id, said before the round trip.
 *
 * M11 deliberately left this out of `validateDevice` — the edit modal cannot change an id, so
 * the wizard is the only place one is ever typed.
 */
export function validateDeviceId(id: string, existing: string[]): DeviceError | null {
  if (!id.trim()) return { field: 'id', key: 'wizard.err.idRequired' }
  if (!ID_PATTERN.test(id)) return { field: 'id', key: 'wizard.err.idPattern' }
  if (existing.includes(id)) return { field: 'id', key: 'wizard.err.idTaken', params: { id } }
  return null
}

// --- PBS discovery ----------------------------------------------------------------

/**
 * The registered PBS this discovered storage already points at, or `null` for a new one.
 *
 * Matched on host + datastore rather than on the storage id: each PVE names the same PBS
 * however it likes (which is exactly why `storages` lives on the PVE), so the id says nothing
 * about identity. Host comparison is case-insensitive and trimmed — PVE stores whatever was
 * typed into the storage form.
 */
export function matchStorage(storage: WizardStorage, pbss: PbsDevice[]): PbsDevice | null {
  const host = storage.host.trim().toLowerCase()
  return (
    pbss.find(
      (p) => p.host.trim().toLowerCase() === host && p.datastore === storage.datastore,
    ) ?? null
  )
}

/**
 * The `{pbsId: storageId}` map for the storages that already map onto a registered PBS.
 *
 * This is the "already-registered → auto-link" half of flow A step 2: it needs no extra step
 * and no credentials, because the device it links to is already configured.
 */
export function linkedStorages(
  storages: WizardStorage[],
  pbss: PbsDevice[],
): Record<string, string> {
  const map: Record<string, string> = {}
  for (const storage of storages) {
    const pbs = matchStorage(storage, pbss)
    if (pbs) map[pbs.id] = storage.storage
  }
  return map
}

/** The discovered storages with no registered counterpart — the ones step 3 can configure. */
export function newStorages(storages: WizardStorage[], pbss: PbsDevice[]): WizardStorage[] {
  return storages.filter((s) => !matchStorage(s, pbss))
}

/**
 * No registered PVE sends backups to this PBS, so Backup routes onto it cannot be built yet.
 *
 * Today this is true of every PBS added through flow B — only flow A's discovery ever fills
 * `pves[].storages` — but it is written as the general check so it stays correct the moment
 * something else pre-links one.
 */
export function isOrphanPbs(pbsId: string, pves: PveDevice[]): boolean {
  return !pves.some((p) => pbsId in p.storages)
}

// --- assembling the POST bodies ----------------------------------------------------

export interface PveDraft {
  id: string
  host: string
  port: number
  cred: CredMode
  /** Transient root — used once by /wizard/pve/connect to mint the token, never persisted. */
  user: string
  password: string
  tokenId: string
  tokenSecret: string
}

export interface PbsDraft {
  id: string
  host: string
  port: number
  datastore: string
  fingerprint: string
  cred: CredMode
  user: string
  password: string
  tokenId: string
  tokenSecret: string
  managedPower: boolean
  mac: string
  wolIface: string
  sshUser: string
  sshKeyPath: string
  /** Install the public key over SSH with the root password, vs the user pasting it themselves. */
  autoInstall: boolean
}

export function freshPveDraft(existing: string[]): PveDraft {
  return {
    id: defaultDeviceId('pve', existing),
    host: '',
    port: 8006,
    cred: 'root',
    user: 'root@pam',
    password: '',
    tokenId: '',
    tokenSecret: '',
  }
}

export function freshPbsDraft(existing: string[], hint = ''): PbsDraft {
  return {
    id: defaultDeviceId('pbs', existing, hint),
    host: '',
    port: 8007,
    datastore: '',
    fingerprint: '',
    cred: 'root',
    user: 'root@pam',
    password: '',
    tokenId: '',
    tokenSecret: '',
    managedPower: true,
    mac: '',
    wolIface: '',
    sshUser: 'root',
    sshKeyPath: '',
    autoInstall: true,
  }
}

/**
 * The `POST /api/devices/pves` body.
 *
 * `storages` is merged in by the caller (auto-linked entries plus the one step 3 just created),
 * because only the flow knows which PBS id the new device ended up with. Token fields hold
 * whatever the flow ended up with: the one the user pasted, or the one `/wizard/pve/connect`
 * minted from the transient root password.
 */
export function pveDeviceFrom(
  draft: PveDraft,
  token: { id: string; secret: string },
  storages: Record<string, string>,
): PveDevice {
  return {
    id: draft.id,
    host: draft.host.trim(),
    port: draft.port,
    verify_tls: false,
    api_token_id: token.id,
    api_token_secret: token.secret,
    storages,
  }
}

/** The `POST /api/devices/pbss` body. Power fields are cleared when Joulenap does not manage
 *  the box: they are unreachable in the form, and storing a stale MAC would be a lie. */
export function pbsDeviceFrom(draft: PbsDraft, token: { id: string; secret: string }): PbsDevice {
  const managed = draft.managedPower
  return {
    id: draft.id,
    host: draft.host.trim(),
    port: draft.port,
    datastore: draft.datastore.trim(),
    fingerprint: draft.fingerprint.trim(),
    api_token_id: token.id,
    api_token_secret: token.secret,
    managed_power: managed,
    mac: managed ? draft.mac.trim() : '',
    wol_broadcast_iface: managed ? draft.wolIface.trim() : '',
    wait_timeout: 180,
    wol_retries: 2,
    poweroff_task_wait: 600,
    ssh_user: managed ? draft.sshUser.trim() || 'root' : 'root',
    ssh_key_path: managed ? draft.sshKeyPath : '',
    external: { first_task_wait: 900, idle_wait: 300 },
  }
}

// --- per-step validation -----------------------------------------------------------

/**
 * What must be filled in before the connection step may talk to the device.
 *
 * Deliberately narrower than `validateDevice`: at step 1 there is no token yet (root mode mints
 * it) and no MAC yet (step 2 detects it), so the full device rules would block a form that is
 * proceeding exactly as designed. The whole-device check runs at save time instead.
 */
export function validateConnectStep(
  draft: PveDraft | PbsDraft,
  existing: string[],
): DeviceError[] {
  const errors: DeviceError[] = []
  const idError = validateDeviceId(draft.id, existing)
  if (idError) errors.push(idError)
  if (!draft.host.trim()) errors.push({ field: 'host', key: 'settings.devices.errHost' })
  if (draft.port < 1 || draft.port > 65535) {
    errors.push({ field: 'port', key: 'settings.devices.errPort' })
  }
  if ('datastore' in draft && !draft.datastore.trim()) {
    errors.push({ field: 'datastore', key: 'settings.devices.errDatastore' })
  }
  if (draft.cred === 'root' && !draft.password) {
    errors.push({ field: 'password', key: 'wizard.err.passwordRequired' })
  }
  if (draft.cred === 'token' && !(draft.tokenId.trim() && draft.tokenSecret)) {
    errors.push({ field: 'tokenId', key: 'wizard.err.tokenRequired' })
  }
  return errors
}

/** The full device rules, right before the POST. Reuses `validateDevice` rather than
 *  re-deriving `config.py`'s rules a third time; the id is the part it does not cover. */
export function validateFinalDevice(
  device: PveDevice | PbsDevice,
  existing: string[],
): DeviceError[] {
  const idError = validateDeviceId(device.id, existing)
  return [...(idError ? [idError] : []), ...validateDevice(device)]
}
