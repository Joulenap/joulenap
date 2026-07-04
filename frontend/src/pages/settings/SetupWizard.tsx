import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { Config, NetInterface } from '../../api/types'
import { ConfirmModal, type ConfirmState } from '../../components/ConfirmModal'
import { Dropdown } from '../../components/Dropdown'
import { useConfig } from '../../config/ConfigContext'
import { c, inputStyle, labelStyle, mono, panelStyle, primaryBtn } from '../../theme'
import { fresh, useWizard, type CardStatus, type Wiz } from '../../wizard/WizardContext'

// True if IPv4 `host` falls within the subnet defined by `address`/`netmask` — used to
// preselect the WoL interface that sits on the PBS's subnet.
function ipInSubnet(host: string, address: string, netmask: string): boolean {
  const toInt = (s: string) => {
    const p = s.split('.').map(Number)
    if (p.length !== 4 || p.some((n) => Number.isNaN(n) || n < 0 || n > 255)) return null
    return ((p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]) >>> 0
  }
  const h = toInt(host)
  const a = toInt(address)
  const m = toInt(netmask)
  if (h === null || a === null || m === null) return false
  return (h & m) === (a & m)
}

// Each card maps to the connection-status checks it (re)validates; used to clear stale
// checks when the user steps back to re-edit an earlier card.
const STEP_CHECKS: Record<number, (keyof Wiz['checks'])[]> = {
  0: ['pve'],
  1: [],
  2: ['pbs', 'token'],
  3: ['wol'],
  4: ['ssh'],
}

const seg = (on: boolean): React.CSSProperties => ({
  flex: 1,
  textAlign: 'center',
  background: on ? c.accent : 'transparent',
  color: on ? c.accentInk : '#9aa2ac',
  border: 'none',
  borderRadius: 6,
  padding: '8px 16px',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
})

const orangeBtn = (enabled: boolean): React.CSSProperties => ({
  background: enabled ? c.accent : '#1d232b',
  color: enabled ? c.accentInk : c.textMuted,
  border: enabled ? 'none' : '1px solid #262d35',
  borderRadius: 7,
  padding: '9px 20px',
  fontSize: 13,
  fontWeight: 600,
  cursor: enabled ? 'pointer' : 'not-allowed',
})

const ghost: React.CSSProperties = {
  background: 'transparent',
  color: c.textMid,
  border: '1px solid #3a434d',
  borderRadius: 7,
  padding: '9px 16px',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
}

function Field({
  label,
  value,
  onChange,
  width = 200,
  type = 'text',
  placeholder,
  monoFont = true,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  width?: number
  type?: string
  placeholder?: string
  monoFont?: boolean
}) {
  return (
    <label style={{ display: 'block', width }}>
      <span style={labelStyle}>{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{ ...inputStyle, padding: '9px 11px', fontFamily: monoFont ? mono : "'IBM Plex Sans', sans-serif" }}
      />
    </label>
  )
}

// The exact commands each quick-setup action performs, shown verbatim so a skeptical user
// can read (or run) them by hand. Not translated — they're literal shell/API calls.
const PVE_CLI = [
  'pveum role add Joulenap -privs "VM.Audit VM.Backup Datastore.Audit \\',
  '  Datastore.AllocateSpace Datastore.Allocate"',
  'pveum user token add root@pam joulenap --privsep 1',
  "pveum acl modify / -role Joulenap -token 'root@pam!joulenap'",
].join('\n')

const pbsCli = (datastore: string) =>
  [
    'proxmox-backup-manager user generate-token root@pam joulenap',
    `proxmox-backup-manager acl update /datastore/${datastore || '<datastore>'} \\`,
    "  DatastoreAdmin --auth-id 'root@pam!joulenap'",
    "proxmox-backup-manager acl update /system Audit --auth-id 'root@pam!joulenap'",
  ].join('\n')

// A saved config counts as "set up" once the connection identity the wizard writes is
// present. Used to show the completed state (and the reset button) after a reload.
function isConfigured(cfg: Config): boolean {
  return !!(cfg.pve.host && cfg.pve.api_token_id && cfg.pbs.host && cfg.pbs.mac)
}

// Rehydrate the wizard into an all-done state from the saved config, so reopening the UI
// shows setup as completed rather than restarting from card 1. Secrets are intentionally
// omitted (they're redacted server-side and not needed to display the completed cards).
function completedFromConfig(cfg: Config): Partial<Wiz> {
  return {
    status: ['done', 'done', 'done', 'done', 'done'],
    checks: { pve: true, pbs: true, token: true, wol: true, ssh: true },
    pveHost: cfg.pve.host,
    node: cfg.pve.node,
    storage: cfg.pve.storage_id,
    tokenId: cfg.pve.api_token_id,
    pbsHost: cfg.pbs.host,
    pbsPort: String(cfg.pbs.port),
    pbsDatastore: cfg.pbs.datastore,
    pbsFp: cfg.pbs.fingerprint,
    pbsTokenId: cfg.pbs.api_token_id,
    pbsUser: cfg.pbs.ssh_user || 'root',
    wolIface: cfg.pbs.wol_broadcast_iface,
    wolMac: cfg.pbs.mac,
    sshKeyPath: cfg.pbs.ssh_key_path,
  }
}

function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation()
  const [done, setDone] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => {
            setDone(true)
            setTimeout(() => setDone(false), 1500)
          },
          () => {},
        )
      }}
      style={{ ...ghost, padding: '5px 11px', fontSize: 12 }}
    >
      {done ? t('settings.setup.buttons.copied') : t('settings.setup.buttons.copy')}
    </button>
  )
}

function CodeBlock({ text }: { text: string }) {
  return (
    <div style={{ position: 'relative' }}>
      <pre
        style={{
          background: c.inputBg,
          border: `1px solid ${c.border}`,
          borderRadius: 7,
          color: '#9aa2ac',
          padding: '10px 12px',
          fontFamily: mono,
          fontSize: 11,
          lineHeight: 1.6,
          margin: 0,
          overflowX: 'auto',
          whiteSpace: 'pre',
        }}
      >
        {text}
      </pre>
      <div style={{ position: 'absolute', top: 7, right: 7 }}>
        <CopyButton text={text} />
      </div>
    </div>
  )
}

// The "why we ask this / what happens to your credentials" microcopy shown atop each card.
function Why({ why, note }: { why: string; note?: string }) {
  return (
    <div style={{ marginTop: 4, fontSize: 12.5, color: c.textDim, lineHeight: 1.5 }}>
      <span>{why}</span>
      {note && (
        <span style={{ display: 'block', marginTop: 4, color: '#8f98a2' }}>{note}</span>
      )}
    </div>
  )
}

// Collapsible "What this will do" disclosure: the exact steps + copy-paste CLI equivalent.
function Disclosure({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginTop: 4 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{ background: 'transparent', border: 'none', color: c.textMid, fontSize: 12, fontWeight: 600, cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', gap: 6 }}
      >
        <span style={{ fontSize: 10 }}>{open ? '▾' : '▸'}</span>
        {t('settings.setup.does.toggle')}
      </button>
      {open && <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>{children}</div>}
    </div>
  )
}

export function SetupWizard() {
  const { t } = useTranslation()
  const { config, save, reload } = useConfig()
  const { w, setW } = useWizard()
  const [busy, setBusy] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ifaces, setIfaces] = useState<NetInterface[]>([])
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  // If the saved config is already set up, show the wizard as completed on (re)mount rather
  // than restarting from card 1 — but never clobber an in-progress session's own state.
  const hydrated = useRef(false)
  useEffect(() => {
    if (hydrated.current || !config || !isConfigured(config)) return
    hydrated.current = true
    setW((s) => (s.pveHost || s.status[1] !== 'locked' ? s : { ...s, ...completedFromConfig(config) }))
  }, [config, setW])

  // Load the host's NICs for the WoL interface dropdown; default to the one whose subnet
  // holds the PBS (if known) else the first, without clobbering a value already chosen.
  useEffect(() => {
    api
      .wizardInterfaces()
      .then((list) => {
        setIfaces(list)
        setW((s) => {
          if (s.wolIface) return s
          const inHost = (ifc: NetInterface) =>
            !!s.pbsHost && ipInSubnet(s.pbsHost, ifc.address, ifc.netmask)
          const pick = list.find(inHost) ?? list[0]
          return pick ? { ...s, wolIface: pick.name } : s
        })
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const patch = (p: Partial<Wiz>) => setW((s) => ({ ...s, ...p }))
  const rapido = w.mode === 'rapido'
  const doneCount = w.status.filter((s) => s === 'done').length
  const allDone = doneCount === 5

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key)
    setError(null)
    try {
      await fn()
    } catch (e) {
      // Surface the backend's message (e.g. PVE auth failure, unreachable host) so a
      // failed step isn't silently a no-op; the connection-status dots stay red too.
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const advance = (i: number, extra: Partial<Wiz> = {}) =>
    setW((s) => {
      const status = s.status.slice() as CardStatus[]
      status[i] = 'done'
      if (i + 1 < status.length) status[i + 1] = 'active'
      return { ...s, status, ...extra }
    })

  // Re-open an earlier card: it becomes active, every later card is locked again and the
  // checks those later steps had validated are cleared (their data is kept). Entered values
  // survive, so the user only has to re-confirm forward from here.
  const goTo = (target: number) =>
    setW((s) => {
      const status = s.status.map((_, i) =>
        i < target ? 'done' : i === target ? 'active' : 'locked',
      ) as CardStatus[]
      const checks = { ...s.checks }
      for (let i = target; i < status.length; i++)
        for (const key of STEP_CHECKS[i]) checks[key] = false
      return { ...s, status, checks }
    })

  const connectPve = () =>
    run('pve', async () => {
      const r = await api.wizardPveConnect({
        host: w.pveHost,
        port: 8006,
        verify_tls: false,
        mode: rapido ? 'root' : 'token',
        username: w.pveUser,
        password: w.pvePass,
        api_token_id: w.tokenId,
        api_token_secret: w.tokenSecret,
      })
      const nodes = r.nodes.map((n) => n.node)
      advance(0, {
        nodes,
        storages: r.storages,
        node: nodes[0] ?? '',
        storage: r.storages[0]?.storage ?? '',
        tokenId: r.token?.id ?? w.tokenId,
        tokenSecret: r.token?.secret ?? w.tokenSecret,
        checks: { ...w.checks, pve: true },
      })
    })

  const confirmNode = () => {
    const s = w.storages.find((x) => x.storage === w.storage)
    advance(1, {
      pbsHost: s?.host ?? '',
      pbsPort: String(s?.port ?? 8007),
      pbsDatastore: s?.datastore ?? '',
      pbsFp: s?.fingerprint ?? '',
    })
  }

  const checkPbs = () =>
    run('pbs', async () => {
      const port = Number(w.pbsPort) || 8007
      const r = await api.wizardPbsCheck(w.pbsHost, port)
      // Quick setup: mint a scoped PBS token from the root creds (same ones used to install
      // the SSH key), so the user never pastes a token. Manual mode keeps the typed token.
      let { pbsTokenId, pbsTokenSecret } = w
      if (rapido) {
        const tok = await api.wizardPbsProvision({
          host: w.pbsHost,
          port,
          username: w.pbsUser,
          password: w.pbsPass,
          datastore: w.pbsDatastore,
        })
        pbsTokenId = tok.id
        pbsTokenSecret = tok.secret
      }
      advance(2, {
        pbsFp: w.pbsFp || r.fingerprint || '',
        pbsTokenId,
        pbsTokenSecret,
        checks: { ...w.checks, pbs: r.reachable, token: !!(pbsTokenId && pbsTokenSecret) },
      })
    })

  const detectMac = () =>
    run('mac', async () => {
      const r = await api.wizardDetectMac(w.pbsHost)
      if (r.mac) patch({ wolMac: r.mac })
    })

  const confirmWol = () => advance(3, { checks: { ...w.checks, wol: true } })

  const genKey = () =>
    run('key', async () => {
      const r = await api.wizardKeygen()
      patch({ sshKey: r.public_key, sshKeyLine: r.authorized_keys_line, sshKeyPath: r.key_path })
    })

  const installKey = () =>
    run('ssh', async () => {
      await api.wizardSshInstall({
        host: w.pbsHost,
        user: w.pbsUser,
        password: w.pbsPass,
        public_key: w.sshKey,
        port: 22,
      })
      advance(4, { checks: { ...w.checks, ssh: true } })
    })

  const markInstalled = () => advance(4, { checks: { ...w.checks, ssh: true } })

  const configured = !!config && isConfigured(config)

  const resetSetup = () =>
    setConfirm({
      title: t('settings.setup.reset.confirmTitle'),
      message: t('settings.setup.reset.confirmMsg'),
      confirmLabel: t('settings.setup.reset.confirmYes'),
      danger: true,
      icon: '↺',
      onConfirm: () =>
        run('reset', async () => {
          await api.wizardReset()
          hydrated.current = true // config is now unconfigured; don't re-hydrate
          setW(fresh('manuale'))
          setSaved(false)
          await reload()
        }),
    })

  async function onSave() {
    if (!config) return
    const next = structuredClone(config)
    next.pve = {
      ...next.pve,
      host: w.pveHost,
      port: 8006,
      node: w.node,
      verify_tls: false,
      api_token_id: w.tokenId,
      api_token_secret: w.tokenSecret,
      storage_id: w.storage,
    }
    next.pbs = {
      ...next.pbs,
      host: w.pbsHost,
      port: Number(w.pbsPort) || 8007,
      datastore: w.pbsDatastore,
      fingerprint: w.pbsFp,
      api_token_id: w.pbsTokenId || next.pbs.api_token_id,
      api_token_secret: w.pbsTokenSecret || next.pbs.api_token_secret,
      mac: w.wolMac,
      wol_broadcast_iface: w.wolIface,
      ssh_user: w.pbsUser || 'root',
      ssh_key_path: w.sshKeyPath || next.pbs.ssh_key_path,
    }
    await run('save', async () => {
      await save(next)
      setSaved(true)
    })
  }

  const cardNames = [
    t('settings.setup.cards.pve'),
    t('settings.setup.cards.node'),
    t('settings.setup.cards.pbs'),
    t('settings.setup.cards.wol'),
    t('settings.setup.cards.ssh'),
  ]

  function cardBody(i: number) {
    if (i === 0)
      return (
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Why why={t('settings.setup.why.pve')} note={rapido ? t('settings.setup.note.pveRoot') : t('settings.setup.note.pveToken')} />
          <Field label={t('settings.setup.fields.pveHost')} value={w.pveHost} onChange={(v) => patch({ pveHost: v })} placeholder="192.168.1.10" />
          {rapido ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              <Field label={t('settings.setup.fields.user')} value={w.pveUser} onChange={(v) => patch({ pveUser: v })} />
              <Field label={t('settings.setup.fields.rootPassword')} value={w.pvePass} onChange={(v) => patch({ pvePass: v })} type="password" monoFont={false} placeholder="••••••••" />
            </div>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              <Field label={t('settings.setup.fields.tokenId')} value={w.tokenId} onChange={(v) => patch({ tokenId: v })} placeholder="root@pam!joulenap" />
              <Field label={t('settings.setup.fields.tokenSecret')} value={w.tokenSecret} onChange={(v) => patch({ tokenSecret: v })} type="password" monoFont={false} placeholder="••••••••" />
            </div>
          )}
          <div>
            <button style={orangeBtn(!!w.pveHost && busy !== 'pve')} disabled={!w.pveHost || busy === 'pve'} onClick={connectPve}>
              {busy === 'pve' ? t('settings.setup.buttons.connecting') : t('settings.setup.buttons.connect')}
            </button>
          </div>
          <Disclosure>
            <span style={{ fontSize: 12.5, color: c.textDim }}>{t('settings.setup.does.pve')}</span>
            <CodeBlock text={PVE_CLI} />
            <span style={{ fontSize: 11.5, color: '#6f7884', lineHeight: 1.5 }}>{t('settings.setup.does.manualHint')}</span>
          </Disclosure>
        </div>
      )
    if (i === 1)
      return (
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Why why={t('settings.setup.why.node')} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end' }}>
          <div style={{ width: 200 }}>
            <span style={labelStyle}>{t('settings.setup.fields.node')}</span>
            <Dropdown value={w.node} options={w.nodes.map((n) => ({ value: n, label: n }))} onChange={(v) => patch({ node: v })} mono />
          </div>
          <div style={{ width: 200 }}>
            <span style={labelStyle}>{t('settings.setup.fields.pbsStorage')}</span>
            <Dropdown value={w.storage} options={w.storages.map((s) => ({ value: s.storage, label: s.storage }))} onChange={(v) => patch({ storage: v })} mono />
          </div>
            <button style={orangeBtn(!!w.storage)} disabled={!w.storage} onClick={confirmNode}>
              {t('settings.setup.buttons.confirm')}
            </button>
          </div>
        </div>
      )
    if (i === 2)
      return (
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Why why={t('settings.setup.why.pbs')} note={rapido ? t('settings.setup.note.pbsRoot') : t('settings.setup.note.pbsToken')} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end' }}>
            <Field label={t('settings.setup.fields.pbsHost')} value={w.pbsHost} onChange={(v) => patch({ pbsHost: v })} width={170} />
            <Field label={t('settings.setup.fields.port')} value={w.pbsPort} onChange={(v) => patch({ pbsPort: v })} width={92} />
            <Field label={t('settings.setup.fields.datastore')} value={w.pbsDatastore} onChange={(v) => patch({ pbsDatastore: v })} width={150} />
          </div>
          {!rapido && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              <Field label={t('settings.setup.fields.pbsToken')} value={w.pbsTokenId} onChange={(v) => patch({ pbsTokenId: v })} placeholder="root@pam!joulenap" />
              <Field label={t('settings.setup.fields.pbsTokenSecret')} value={w.pbsTokenSecret} onChange={(v) => patch({ pbsTokenSecret: v })} type="password" monoFont={false} placeholder="••••••••" />
            </div>
          )}
          {rapido && (
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                <Field label={t('settings.setup.fields.pbsRootUser')} value={w.pbsUser} onChange={(v) => patch({ pbsUser: v })} />
                <Field label={t('settings.setup.fields.pbsRootPassword')} value={w.pbsPass} onChange={(v) => patch({ pbsPass: v })} type="password" monoFont={false} placeholder="••••••••" />
              </div>
              <span style={{ fontSize: 11, color: '#6f7884' }}>{t('settings.setup.hints.pbsRoot')}</span>
            </>
          )}
          <label style={{ display: 'block' }}>
            <span style={labelStyle}>{t('settings.setup.fields.fingerprint')}</span>
            <div style={{ background: c.inputBg, border: `1px solid ${c.border}`, borderRadius: 7, color: c.textDim, padding: '9px 11px', fontFamily: mono, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {w.pbsFp || '—'}
            </div>
          </label>
          <div>
            <button
              style={orangeBtn(!!w.pbsHost && (!rapido || !!w.pbsPass) && busy !== 'pbs')}
              disabled={!w.pbsHost || (rapido && !w.pbsPass) || busy === 'pbs'}
              onClick={checkPbs}
            >
              {busy === 'pbs' ? t('settings.setup.buttons.connecting') : t('settings.setup.buttons.connect')}
            </button>
          </div>
          <Disclosure>
            <span style={{ fontSize: 12.5, color: c.textDim }}>{t('settings.setup.does.pbs')}</span>
            <CodeBlock text={pbsCli(w.pbsDatastore)} />
            <span style={{ fontSize: 11.5, color: '#6f7884', lineHeight: 1.5 }}>{t('settings.setup.does.tighten')}</span>
          </Disclosure>
        </div>
      )
    if (i === 3)
      return (
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Why why={t('settings.setup.why.wol')} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end' }}>
            <div style={{ width: 230 }}>
              <span style={labelStyle}>{t('settings.setup.fields.interface')}</span>
              {ifaces.length ? (
                <Dropdown
                  value={w.wolIface}
                  options={ifaces.map((ifc) => ({ value: ifc.name, label: `${ifc.name} — ${ifc.address}` }))}
                  onChange={(v) => patch({ wolIface: v })}
                  mono
                />
              ) : (
                <Field label="" value={w.wolIface} onChange={(v) => patch({ wolIface: v })} width={230} />
              )}
            </div>
            <Field label={t('settings.setup.fields.mac')} value={w.wolMac} onChange={(v) => patch({ wolMac: v })} width={220} />
          </div>
          <span style={{ fontSize: 11, color: '#6f7884' }}>{t('settings.setup.hints.wolIface')}</span>
          <div style={{ display: 'flex', gap: 10 }}>
            <button style={ghost} onClick={detectMac}>
              {busy === 'mac' ? t('settings.setup.buttons.detecting') : t('settings.setup.buttons.detectMac')}
            </button>
            <button style={orangeBtn(true)} onClick={confirmWol}>
              {t('settings.setup.buttons.confirm')}
            </button>
          </div>
        </div>
      )
    // i === 4: SSH
    return (
      <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Why why={t('settings.setup.why.ssh')} note={t('settings.setup.note.ssh')} />
        {!w.sshKey ? (
          <div>
            <button style={ghost} onClick={genKey}>
              {busy === 'key' ? t('settings.setup.buttons.generating') : t('settings.setup.buttons.genKey')}
            </button>
          </div>
        ) : (
          <>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ ...labelStyle, marginBottom: 0 }}>{t('settings.setup.fieldsAuthLine')}</span>
                <CopyButton text={w.sshKeyLine} />
              </div>
              <div style={{ background: c.inputBg, border: `1px solid ${c.border}`, borderRadius: 7, color: '#9aa2ac', padding: '10px 12px', fontFamily: mono, fontSize: 11, lineHeight: 1.5, wordBreak: 'break-all' }}>
                {w.sshKeyLine}
              </div>
            </div>
            {rapido ? (
              <div>
                <button style={orangeBtn(busy !== 'ssh')} disabled={busy === 'ssh'} onClick={installKey}>
                  {busy === 'ssh' ? t('settings.setup.buttons.installing') : t('settings.setup.buttons.install')}
                </button>
              </div>
            ) : (
              <div>
                <span style={{ display: 'block', fontSize: 11, color: c.textDim, marginBottom: 8 }}>{t('settings.setup.hints.sshManual')}</span>
                <button style={orangeBtn(true)} onClick={markInstalled}>
                  {t('settings.setup.buttons.verify')}
                </button>
              </div>
            )}
            <Disclosure>
              <span style={{ fontSize: 12.5, color: c.textDim }}>{t('settings.setup.does.ssh')}</span>
              <CodeBlock text={`echo '${w.sshKeyLine}' >> /root/.ssh/authorized_keys`} />
            </Disclosure>
          </>
        )}
      </div>
    )
  }

  const checkMeta: [keyof Wiz['checks'], string][] = [
    ['pve', t('settings.setup.checks.pve')],
    ['pbs', t('settings.setup.checks.pbs')],
    ['wol', t('settings.setup.checks.wol')],
    ['ssh', t('settings.setup.checks.ssh')],
    ['token', t('settings.setup.checks.token')],
  ]

  return (
    <div style={{ ...panelStyle, padding: '22px 24px', maxWidth: 820 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <span style={{ display: 'block', fontSize: 16, fontWeight: 700, marginBottom: 5 }}>{t('settings.setup.title')}</span>
          <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.5, maxWidth: 440 }}>
            {t('settings.setup.subtitle')}
          </span>
        </div>
        <span style={{ fontFamily: mono, fontSize: 11, fontWeight: 600, color: '#9aa2ac', background: c.inputBg, border: '1px solid #262d35', borderRadius: 7, padding: '6px 11px', whiteSpace: 'nowrap' }}>
          {t('settings.setup.progress', { done: doneCount })}
        </span>
      </div>

      {/* Token setup first + default: "no root required" is the more trustworthy opener. */}
      <div style={{ display: 'inline-flex', background: c.inputBg, border: `1px solid ${c.inputBorder}`, borderRadius: 8, padding: 3, gap: 3, margin: '16px 0 14px' }}>
        <button style={seg(!rapido)} onClick={() => setW((s) => ({ ...s, mode: 'manuale' }))}>
          {t('settings.setup.manual')}
        </button>
        <button style={seg(rapido)} onClick={() => setW((s) => ({ ...s, mode: 'rapido' }))}>
          {t('settings.setup.quick')}
        </button>
      </div>

      <div style={{ background: 'rgba(63,178,127,.07)', border: '1px solid rgba(63,178,127,.22)', borderRadius: 10, padding: '13px 15px', marginBottom: 16 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600, color: c.green, marginBottom: 5 }}>
          <span aria-hidden>🔒</span>
          {t('settings.setup.trust.title')}
        </span>
        <span style={{ display: 'block', fontSize: 12.5, color: c.textDim, lineHeight: 1.55 }}>
          {t('settings.setup.trust.body')}
        </span>
        <span style={{ display: 'block', fontSize: 11.5, color: '#6f7884', marginTop: 7, fontFamily: mono }}>
          {t('settings.setup.trust.source')}
        </span>
      </div>

      {configured && (
        <div style={{ background: 'rgba(232,131,15,.08)', border: '1px solid rgba(232,131,15,.28)', borderRadius: 10, padding: '11px 15px', marginBottom: 16, fontSize: 12.5, color: c.textMid, lineHeight: 1.5 }}>
          {t('settings.setup.completeBanner')}
        </div>
      )}

      {error && (
        <div
          style={{
            background: 'rgba(229,103,91,.1)',
            border: '1px solid rgba(229,103,91,.32)',
            borderRadius: 8,
            color: c.red,
            fontSize: 12.5,
            lineHeight: 1.5,
            padding: '10px 13px',
            marginBottom: 14,
            wordBreak: 'break-word',
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {w.status.map((st, i) => {
          const act = st === 'active'
          const done = st === 'done'
          const locked = st === 'locked'
          return (
            <div
              key={i}
              style={{
                background: c.panelAlt,
                border: `1px solid ${act ? '#2c343d' : done ? 'rgba(63,178,127,.28)' : c.borderSoft}`,
                borderRadius: 10,
                padding: '16px 18px',
                opacity: locked ? 0.5 : 1,
                transition: 'opacity .3s, border-color .3s',
              }}
            >
              <div style={{ display: 'flex', gap: 13, alignItems: 'flex-start' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 28,
                    height: 28,
                    flex: '0 0 auto',
                    borderRadius: '50%',
                    fontFamily: mono,
                    fontSize: 13,
                    fontWeight: 600,
                    background: act ? c.accent : done ? 'rgba(63,178,127,.18)' : '#222a32',
                    color: act ? c.accentInk : done ? c.green : c.textFaint,
                  }}
                >
                  {done ? '✓' : i + 1}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{cardNames[i]}</span>
                    <span
                      style={{
                        fontFamily: mono,
                        fontSize: 10,
                        fontWeight: 600,
                        padding: '2px 8px',
                        borderRadius: 999,
                        color: act ? c.accent : done ? c.green : '#6f7884',
                        background: act ? 'rgba(232,131,15,.14)' : done ? 'rgba(63,178,127,.14)' : 'rgba(255,255,255,.04)',
                        border: `1px solid ${act ? 'rgba(232,131,15,.2)' : done ? 'rgba(63,178,127,.25)' : '#262d35'}`,
                      }}
                    >
                      {act ? t('settings.setup.inProgress') : done ? t('settings.setup.completed') : t('settings.setup.locked')}
                    </span>
                    {done && (
                      <button
                        onClick={() => goTo(i)}
                        style={{ marginLeft: 'auto', background: 'transparent', border: 'none', color: c.textDim, fontSize: 12, fontWeight: 600, cursor: 'pointer', padding: '2px 4px' }}
                      >
                        {t('common.edit')}
                      </button>
                    )}
                  </div>
                  {act && (
                    <>
                      {cardBody(i)}
                      {i > 0 && (
                        <button onClick={() => goTo(i - 1)} style={{ ...ghost, marginTop: 12, padding: '8px 14px' }}>
                          ← {t('settings.setup.buttons.back')}
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <div style={{ background: c.panelAlt, border: `1px solid ${c.borderSoft}`, borderRadius: 10, padding: '16px 18px', marginTop: 18 }}>
        <span style={{ ...labelStyle, marginBottom: 14 }}>{t('settings.setup.connectionStatus')}</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '11px 26px', marginBottom: 18 }}>
          {checkMeta.map(([key, label]) => {
            const ok = w.checks[key]
            return (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                <span style={{ width: 9, height: 9, borderRadius: '50%', background: ok ? c.green : '#6b7480', boxShadow: ok ? '0 0 0 3px rgba(63,178,127,.18)' : 'none', flex: '0 0 auto' }} />
                <span style={{ fontSize: 13, color: ok ? '#cdd3da' : c.textDim }}>{label}</span>
                <span style={{ fontFamily: mono, fontSize: 11, color: ok ? c.green : '#6f7884' }}>
                  {ok ? t('settings.setup.ok') : t('settings.setup.pending')}
                </span>
              </div>
            )
          })}
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'space-between', borderTop: `1px solid ${c.borderSoft}`, paddingTop: 15, alignItems: 'center' }}>
          <div>
            {configured && (
              <button
                onClick={resetSetup}
                disabled={busy === 'reset'}
                style={{ ...ghost, padding: '9px 16px', borderColor: 'rgba(229,103,91,.4)', color: c.red }}
              >
                {t('settings.setup.reset.button')}
              </button>
            )}
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {saved && <span style={{ fontSize: 12, color: c.green }}>{t('settings.setup.saved')}</span>}
            <button onClick={onSave} disabled={!allDone || busy === 'save'} style={{ ...primaryBtn, padding: '9px 22px', background: allDone ? c.accent : '#1d232b', color: allDone ? c.accentInk : c.textMuted, border: allDone ? 'none' : '1px solid #262d35', cursor: allDone ? 'pointer' : 'not-allowed' }}>
              {t('settings.setup.save')}
            </button>
          </div>
        </div>
      </div>

      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </div>
  )
}
