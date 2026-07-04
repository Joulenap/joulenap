import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type { NotificationsConfig } from '../../api/types'
import { Toggle } from '../../components/Toggle'
import { useConfig } from '../../config/ConfigContext'
import { c, ghostBtn, inputStyle, labelStyle, panelStyle, primaryBtn } from '../../theme'

// M7: Apprise-backed notification channels. The friendly forms here are turned into
// Apprise URLs server-side (backend/app/notify); secrets arrive redacted and are sent
// back untouched to keep the stored value (see config restore_secrets).

type ChannelKey = 'telegram' | 'ntfy' | 'email' | 'discord'

const REDACTED = '***REDACTED***'

export function Notifications() {
  const { t } = useTranslation()
  const { config, save } = useConfig()

  const [draft, setDraft] = useState<NotificationsConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [savedNote, setSavedNote] = useState(false)
  const [testState, setTestState] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null)
  const [replacing, setReplacing] = useState(false)

  // (Re)seed the editable draft whenever the loaded config changes.
  useEffect(() => {
    if (config) {
      setDraft(structuredClone(config.notifications))
      setReplacing(false)
    }
  }, [config])

  const dirty = useMemo(
    () => !!config && !!draft && JSON.stringify(draft) !== JSON.stringify(config.notifications),
    [config, draft],
  )

  if (!config || !draft) return null

  function patch(next: Partial<NotificationsConfig>) {
    setDraft((d) => (d ? { ...d, ...next } : d))
    setSavedNote(false)
    setTestState(null)
  }

  function patchChannel<K extends ChannelKey>(key: K, next: Partial<NotificationsConfig[K]>) {
    setDraft((d) => (d ? { ...d, [key]: { ...d[key], ...next } } : d))
    setSavedNote(false)
    setTestState(null)
  }

  async function onSave() {
    if (!config) return
    setBusy(true)
    try {
      await save({ ...config, notifications: draft! })
      setSavedNote(true)
    } finally {
      setBusy(false)
    }
  }

  async function onTest() {
    setBusy(true)
    setTestState(null)
    try {
      const r = await api.notifyTest()
      setTestState({ kind: 'ok', msg: t('settings.notifications.testOk', { n: r.channels }) })
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : t('settings.notifications.testFail')
      setTestState({ kind: 'err', msg })
    } finally {
      setBusy(false)
    }
  }

  const field = (label: string, value: string, onChange: (v: string) => void, opts?: { type?: string; placeholder?: string }) => (
    <label style={{ display: 'block' }}>
      <span style={labelStyle}>{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        type={opts?.type ?? 'text'}
        placeholder={opts?.placeholder}
        autoComplete="off"
        spellCheck={false}
        style={inputStyle}
      />
    </label>
  )

  const channelCard = (key: ChannelKey, title: string, body: React.ReactNode) => {
    const ch = draft[key]
    return (
      <div
        style={{
          background: c.panelAlt,
          border: `1px solid ${c.borderSoft}`,
          borderRadius: 10,
          padding: '16px 18px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: ch.enabled ? 16 : 0 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: c.textMid }}>{title}</span>
          <Toggle on={ch.enabled} onClick={() => patchChannel(key, { enabled: !ch.enabled } as never)} />
        </div>
        {ch.enabled && <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>{body}</div>}
      </div>
    )
  }

  const ns = 'settings.notifications'

  return (
    <div style={{ ...panelStyle, padding: '24px 26px', maxWidth: 640 }}>
      <span style={{ display: 'block', fontSize: 16, fontWeight: 700, marginBottom: 5 }}>
        {t(`${ns}.title`)}
      </span>
      <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.5, marginBottom: 22 }}>
        {t(`${ns}.subtitle`)}
      </span>

      {/* events */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 22 }}>
        {(['on_success', 'on_failure'] as const).map((k) => (
          <div key={k} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: c.textMid }}>
                {t(`${ns}.${k === 'on_success' ? 'onSuccess' : 'onFailure'}`)}
              </span>
              <span style={{ display: 'block', fontSize: 12, color: c.textFaint, marginTop: 2 }}>
                {t(`${ns}.${k === 'on_success' ? 'onSuccessDesc' : 'onFailureDesc'}`)}
              </span>
            </div>
            <Toggle on={draft[k]} onClick={() => patch({ [k]: !draft[k] } as Partial<NotificationsConfig>)} />
          </div>
        ))}
      </div>

      {/* channels */}
      <span style={{ ...labelStyle, marginBottom: 12 }}>{t(`${ns}.channels`)}</span>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {channelCard(
          'telegram',
          t(`${ns}.telegramTitle`),
          <>
            {field(t(`${ns}.botToken`), draft.telegram.bot_token, (v) => patchChannel('telegram', { bot_token: v }), { type: 'password' })}
            {field(t(`${ns}.chatId`), draft.telegram.chat_id, (v) => patchChannel('telegram', { chat_id: v }))}
          </>,
        )}

        {channelCard(
          'ntfy',
          t(`${ns}.ntfyTitle`),
          <>
            {field(t(`${ns}.ntfyUrl`), draft.ntfy.url, (v) => patchChannel('ntfy', { url: v }), { placeholder: 'https://ntfy.sh' })}
            {field(t(`${ns}.ntfyTopic`), draft.ntfy.topic, (v) => patchChannel('ntfy', { topic: v }))}
          </>,
        )}

        {channelCard(
          'email',
          t(`${ns}.emailTitle`),
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
              {field(t(`${ns}.smtpHost`), draft.email.smtp_host, (v) => patchChannel('email', { smtp_host: v }))}
              {field(t(`${ns}.smtpPort`), String(draft.email.smtp_port), (v) =>
                patchChannel('email', { smtp_port: Number(v) || 0 }), { type: 'number' })}
            </div>
            {field(t(`${ns}.smtpUser`), draft.email.smtp_user, (v) => patchChannel('email', { smtp_user: v }))}
            {field(t(`${ns}.smtpPassword`), draft.email.smtp_password, (v) => patchChannel('email', { smtp_password: v }), { type: 'password' })}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {field(t(`${ns}.fromAddr`), draft.email.from_addr, (v) => patchChannel('email', { from_addr: v }))}
              {field(t(`${ns}.toAddr`), draft.email.to_addr, (v) => patchChannel('email', { to_addr: v }))}
            </div>
          </>,
        )}

        {channelCard(
          'discord',
          t(`${ns}.discordTitle`),
          field(t(`${ns}.webhookUrl`), draft.discord.webhook_url, (v) => patchChannel('discord', { webhook_url: v }), { type: 'password' }),
        )}

        {/* custom Apprise URLs — write-only: existing entries arrive redacted, so we either
            keep them untouched or replace the whole list (a mixed list is rejected server-side). */}
        <div style={{ background: c.panelAlt, border: `1px solid ${c.borderSoft}`, borderRadius: 10, padding: '16px 18px' }}>
          <span style={{ display: 'block', fontSize: 14, fontWeight: 600, color: c.textMid, marginBottom: 4 }}>
            {t(`${ns}.customTitle`)}
          </span>
          <span style={{ display: 'block', fontSize: 12, color: c.textFaint, marginBottom: 12 }}>
            {t(`${ns}.customDesc`)}
          </span>
          {draft.custom_urls.length > 0 && draft.custom_urls.every((u) => u === REDACTED) && !replacing ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ fontSize: 13, color: c.textMuted }}>
                {t(`${ns}.customConfigured`, { n: draft.custom_urls.length })}
              </span>
              <button
                type="button"
                onClick={() => { setReplacing(true); patch({ custom_urls: [] }) }}
                style={{ ...ghostBtn, padding: '6px 14px' }}
              >
                {t(`${ns}.customReplace`)}
              </button>
            </div>
          ) : (
            <textarea
              value={draft.custom_urls.join('\n')}
              onChange={(e) =>
                patch({ custom_urls: e.target.value.split('\n').map((l) => l.trim()).filter(Boolean) })
              }
              rows={3}
              spellCheck={false}
              placeholder={'tgram://token/chatid\nntfy://host/topic\ngotify://host/token'}
              style={{ ...inputStyle, resize: 'vertical', fontFamily: "'IBM Plex Mono', monospace", fontSize: 12 }}
            />
          )}
        </div>
      </div>

      {/* actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 22, flexWrap: 'wrap' }}>
        <button onClick={onSave} disabled={!dirty || busy} style={{
          ...primaryBtn,
          padding: '10px 24px',
          background: dirty ? c.accent : '#1d232b',
          color: dirty ? c.accentInk : c.textMuted,
          border: dirty ? 'none' : '1px solid #262d35',
          cursor: dirty ? 'pointer' : 'not-allowed',
        }}>
          {t(`${ns}.apply`)}
        </button>
        <button onClick={onTest} disabled={busy || dirty} style={{
          ...ghostBtn,
          padding: '10px 20px',
          opacity: dirty ? 0.5 : 1,
          cursor: dirty ? 'not-allowed' : 'pointer',
        }} title={dirty ? t(`${ns}.saveFirst`) : undefined}>
          {t(`${ns}.sendTest`)}
        </button>
        {savedNote && !dirty && <span style={{ fontSize: 12, color: c.green }}>{t(`${ns}.saved`)}</span>}
        {testState && (
          <span style={{ fontSize: 12, color: testState.kind === 'ok' ? c.green : c.red }}>{testState.msg}</span>
        )}
      </div>
    </div>
  )
}
