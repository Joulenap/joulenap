import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import { REDACTED, type ChannelOutcome, type NotificationsConfig } from '../../api/types'
import { Toggle } from '../../components/Toggle'
import { useConfig } from '../../config/ConfigContext'
import { useRegisterDirty } from '../../shell/UnsavedGuard'
import { channelLabel } from '../../utils/notifyChannels'

// M7: Apprise-backed notification channels. The friendly forms here are turned into Apprise
// URLs server-side (backend/app/notify); secrets arrive redacted and are sent back untouched
// to keep the stored value (see config restore_secrets).

type ChannelKey = 'telegram' | 'ntfy' | 'email' | 'discord'

const ns = 'settings.notifications'

export function Notifications() {
  const { t } = useTranslation()
  const { config, save } = useConfig()

  const [draft, setDraft] = useState<NotificationsConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [testState, setTestState] = useState<{ channels: ChannelOutcome[] } | { error: string } | null>(null)
  const [replacing, setReplacing] = useState(false)
  const [newUrl, setNewUrl] = useState('')

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
  useRegisterDirty(dirty)

  if (!config || !draft) return null

  function patch(next: Partial<NotificationsConfig>) {
    setDraft((d) => (d ? { ...d, ...next } : d))
    setSaved(false)
    setSaveErr(null)
    setTestState(null)
  }

  function patchChannel<K extends ChannelKey>(key: K, next: Partial<NotificationsConfig[K]>) {
    setDraft((d) => (d ? { ...d, [key]: { ...d[key], ...next } } : d))
    setSaved(false)
    setSaveErr(null)
    setTestState(null)
  }

  async function onSave() {
    if (!config) return
    setBusy(true)
    setSaveErr(null)
    try {
      await save({ ...config, notifications: draft! })
      setSaved(true)
    } catch (e) {
      setSaveErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  async function onTest() {
    setBusy(true)
    setTestState(null)
    try {
      setTestState({ channels: (await api.notifyTest()).channels })
    } catch (e) {
      // Only transport/auth failures land here — delivery outcomes come back as 200.
      setTestState({ error: e instanceof ApiError ? e.message : t(`${ns}.testFailed`) })
    } finally {
      setBusy(false)
    }
  }

  const field = (
    id: string,
    label: string,
    value: string,
    onChange: (v: string) => void,
    opts: { type?: string; placeholder?: string; mono?: boolean } = {},
  ) => (
    <div className="field" key={id}>
      <label htmlFor={`ntf-${id}`}>{label}</label>
      <input
        id={`ntf-${id}`}
        type={opts.type ?? 'text'}
        className={opts.mono ? 'in-mono' : undefined}
        value={value}
        placeholder={opts.placeholder}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )

  /** A channel card: the toggle in the header is what reveals its fields, per the mockup. */
  const channel = (key: ChannelKey, title: string, hint: string, body: React.ReactNode, wide = false) => {
    const ch = draft[key]
    return (
      <div className="chan">
        <label className="chan-hd">
          <Toggle on={ch.enabled} onClick={() => patchChannel(key, { enabled: !ch.enabled } as never)} size="sm" />
          <span className="chan-name">{title}</span>
          <span className="chan-hint">{hint}</span>
        </label>
        {ch.enabled && <div className={`chan-fields${wide ? '' : ' frow'}`}>{body}</div>}
      </div>
    )
  }

  // The stored list arrives fully redacted and is write-only: the backend rejects a mixed
  // list, so it is either kept untouched or replaced whole (config.py::_unmask).
  const storedRedacted =
    draft.custom_urls.length > 0 && draft.custom_urls.every((u) => u === REDACTED) && !replacing

  const setUrls = (urls: string[]) => patch({ custom_urls: urls })

  return (
    <div>
      <section className="panel">
        <div className="panel-hd">
          <h2>{t(`${ns}.events`)}</h2>
          <span className="panel-hint">{t(`${ns}.eventsHint`)}</span>
        </div>
        <div className="panel-bd stack tight">
          {(['on_success', 'on_failure'] as const).map((k) => (
            <label className="tglrow" key={k}>
              <Toggle
                on={draft[k]}
                onClick={() => patch({ [k]: !draft[k] } as Partial<NotificationsConfig>)}
                size="sm"
              />
              <span>{t(`${ns}.${k === 'on_success' ? 'onSuccess' : 'onFailure'}`)}</span>
            </label>
          ))}
          <div className="save-row" style={{ justifyContent: 'flex-start' }}>
            <button
              type="button"
              className="btn"
              disabled={busy || dirty}
              title={dirty ? t(`${ns}.saveFirst`) : undefined}
              onClick={() => void onTest()}
            >
              {t(`${ns}.sendTest`)}
            </button>
            {testState && 'error' in testState && <span className="err-note">{testState.error}</span>}
            {testState && 'channels' in testState && testState.channels.length === 0 && (
              <span className="help">{t(`${ns}.testNoChannels`)}</span>
            )}
            {testState && 'channels' in testState && testState.channels.length > 0 && (
              <span className="help">
                {testState.channels.map((ch, i) => (
                  <span key={ch.channel}>
                    {i > 0 && ' · '}
                    <span className={ch.ok ? 'state-on' : 'err-note'}>
                      {channelLabel(ch.channel, t)} {ch.ok ? '✓' : `✗ ${ch.error ?? t(`${ns}.testChannelFailed`)}`}
                    </span>
                  </span>
                ))}
              </span>
            )}
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-hd">
          <h2>{t(`${ns}.channels`)}</h2>
        </div>
        <div className="panel-bd stack tight">
          {channel(
            'telegram',
            t(`${ns}.telegramTitle`),
            t(`${ns}.telegramHint`),
            <>
              {field('tg-token', t(`${ns}.botToken`), draft.telegram.bot_token, (v) =>
                patchChannel('telegram', { bot_token: v }), { type: 'password' })}
              {field('tg-chat', t(`${ns}.chatId`), draft.telegram.chat_id, (v) =>
                patchChannel('telegram', { chat_id: v }), { mono: true })}
            </>,
          )}

          {channel(
            'ntfy',
            t(`${ns}.ntfyTitle`),
            t(`${ns}.ntfyHint`),
            <>
              {field('ntfy-url', t(`${ns}.ntfyUrl`), draft.ntfy.url, (v) => patchChannel('ntfy', { url: v }), {
                placeholder: 'https://ntfy.sh',
                mono: true,
              })}
              {field('ntfy-topic', t(`${ns}.ntfyTopic`), draft.ntfy.topic, (v) =>
                patchChannel('ntfy', { topic: v }), { placeholder: 'homelab', mono: true })}
            </>,
          )}

          {channel(
            'email',
            t(`${ns}.emailTitle`),
            t(`${ns}.emailHint`),
            <>
              {field('smtp-host', t(`${ns}.smtpHost`), draft.email.smtp_host, (v) =>
                patchChannel('email', { smtp_host: v }), { placeholder: 'smtp.example.com', mono: true })}
              {field('smtp-port', t(`${ns}.smtpPort`), String(draft.email.smtp_port), (v) =>
                patchChannel('email', { smtp_port: Number(v) || 0 }), { type: 'number', mono: true })}
              {field('smtp-user', t(`${ns}.smtpUser`), draft.email.smtp_user, (v) =>
                patchChannel('email', { smtp_user: v }))}
              {field('smtp-pass', t(`${ns}.smtpPassword`), draft.email.smtp_password, (v) =>
                patchChannel('email', { smtp_password: v }), { type: 'password' })}
              {field('smtp-from', t(`${ns}.fromAddr`), draft.email.from_addr, (v) =>
                patchChannel('email', { from_addr: v }))}
              {field('smtp-to', t(`${ns}.toAddr`), draft.email.to_addr, (v) =>
                patchChannel('email', { to_addr: v }))}
            </>,
          )}

          {channel(
            'discord',
            t(`${ns}.discordTitle`),
            t(`${ns}.discordHint`),
            field('discord-url', t(`${ns}.webhookUrl`), draft.discord.webhook_url, (v) =>
              patchChannel('discord', { webhook_url: v }), {
              type: 'password',
              placeholder: 'https://discord.com/api/webhooks/…',
            }),
            true,
          )}

          <div className="chan">
            <div className="chan-hd static">
              <span className="chan-name">{t(`${ns}.customTitle`)}</span>
              <span className="chan-hint">{t(`${ns}.customDesc`)}</span>
            </div>
            <div className="chan-fields stack tight">
              {storedRedacted ? (
                <div className="url-row">
                  <span className="help spacer">
                    {t(`${ns}.customConfigured`, { n: draft.custom_urls.length })}
                  </span>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      setReplacing(true)
                      setUrls([])
                    }}
                  >
                    {t(`${ns}.customReplace`)}
                  </button>
                </div>
              ) : (
                <>
                  {draft.custom_urls.map((url, i) => (
                    <div className="url-row" key={i}>
                      <input
                        className="in-mono"
                        value={url}
                        spellCheck={false}
                        onChange={(e) =>
                          setUrls(draft.custom_urls.map((u, j) => (j === i ? e.target.value : u)))
                        }
                      />
                      <button
                        type="button"
                        className="btn btn-sm btn-danger-ghost"
                        aria-label={t('common.remove')}
                        onClick={() => setUrls(draft.custom_urls.filter((_, j) => j !== i))}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  <div className="url-row">
                    <input
                      className="in-mono"
                      value={newUrl}
                      spellCheck={false}
                      placeholder="pover://user@token · slack://… · json://…"
                      onChange={(e) => setNewUrl(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key !== 'Enter' || !newUrl.trim()) return
                        e.preventDefault()
                        setUrls([...draft.custom_urls, newUrl.trim()])
                        setNewUrl('')
                      }}
                    />
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={!newUrl.trim()}
                      onClick={() => {
                        setUrls([...draft.custom_urls, newUrl.trim()])
                        setNewUrl('')
                      }}
                    >
                      {t(`${ns}.customAdd`)}
                    </button>
                  </div>
                  {/* Only reachable after "Replace all" cleared a stored (redacted) list: let
                      the user back out and keep the existing URLs instead of wiping them. */}
                  {replacing && config.notifications.custom_urls.length > 0 && (
                    <button
                      type="button"
                      className="btn btn-sm"
                      style={{ alignSelf: 'flex-start' }}
                      onClick={() => {
                        setReplacing(false)
                        setUrls(config.notifications.custom_urls)
                      }}
                    >
                      {t(`${ns}.customCancel`)}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>

          <div className="save-row">
            {saveErr && <span className="err-note">{saveErr}</span>}
            {saved && !dirty && <span className="ok-note">{t(`${ns}.saved`)}</span>}
            {dirty && !saveErr && <span className="help">{t('common.unsavedChanges')}</span>}
            <button
              type="button"
              className="btn btn-accent"
              disabled={!dirty || busy}
              onClick={() => void onSave()}
            >
              {t('common.save')}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
