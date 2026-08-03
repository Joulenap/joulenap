import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, api } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import { Dropdown, type Option } from '../../components/Dropdown'
import { useConfig } from '../../config/ConfigContext'
import { useRegisterDirty } from '../../shell/UnsavedGuard'
import { TIMEZONES } from '../../utils/timezones'

const LANGS = [
  { value: 'en', label: 'English' },
  { value: 'it', label: 'Italiano' },
]

const ns = 'settings.account'

/**
 * Credentials + Localization, the two panels the mockup puts on this tab. The dedicated
 * "Localization" tab is gone; its language/timezone panel moved here verbatim, including the
 * rule that keeps a hand-edited IANA name that is not in the curated list.
 */
export function Account() {
  return (
    <div>
      <Credentials />
      <Localization />
    </div>
  )
}

function Credentials() {
  const { t } = useTranslation()
  const { username, setUsername } = useAuth()

  const [user, setUser] = useState(username ?? '')
  const [current, setCurrent] = useState('')
  const [pass, setPass] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const dirty = user !== (username ?? '') || pass !== '' || confirm !== ''
  useRegisterDirty(dirty)

  useEffect(() => setUser(username ?? ''), [username])

  const mismatch = pass !== '' && confirm !== pass

  async function onSave() {
    setBusy(true)
    setErr(null)
    try {
      const u = await api.updateAccount(current, user.trim() || (username ?? ''), pass || undefined)
      setUsername(u.username)
      setCurrent('')
      setPass('')
      setConfirm('')
      setSaved(true)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const edit = (v: () => void) => {
    v()
    setSaved(false)
    setErr(null)
  }

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t(`${ns}.title`)}</h2>
        <span className="panel-hint">{t(`${ns}.subtitle`)}</span>
      </div>
      <div className="panel-bd stack">
        <div className="frow">
          <div className="field">
            <label htmlFor="acc-user">{t(`${ns}.username`)}</label>
            <input
              id="acc-user"
              value={user}
              autoComplete="username"
              onChange={(e) => edit(() => setUser(e.target.value))}
            />
          </div>
          {/* The mockup has no current-password field, but PUT /api/account requires one
              (BE-S9) — without it the form cannot save at all. */}
          <div className="field">
            <label htmlFor="acc-current">{t(`${ns}.currentPassword`)}</label>
            <input
              id="acc-current"
              type="password"
              value={current}
              autoComplete="current-password"
              onChange={(e) => edit(() => setCurrent(e.target.value))}
            />
          </div>
          <div className="field">
            <label htmlFor="acc-new">{t(`${ns}.newPassword`)}</label>
            <input
              id="acc-new"
              type="password"
              value={pass}
              placeholder={t(`${ns}.keepPlaceholder`)}
              autoComplete="new-password"
              onChange={(e) => edit(() => setPass(e.target.value))}
            />
          </div>
          <div className={`field${mismatch ? ' err' : ''}`}>
            <label htmlFor="acc-confirm">{t(`${ns}.confirmPassword`)}</label>
            <input
              id="acc-confirm"
              type="password"
              value={confirm}
              autoComplete="new-password"
              onChange={(e) => edit(() => setConfirm(e.target.value))}
            />
            {mismatch && <span className="err-note">{t(`${ns}.mismatch`)}</span>}
          </div>
        </div>
        <div className="save-row">
          {err && <span className="err-note">{err}</span>}
          {saved && !dirty && <span className="ok-note">{t(`${ns}.saved`)}</span>}
          {dirty && !err && <span className="help">{t('common.unsavedChanges')}</span>}
          <button
            type="button"
            className="btn btn-accent"
            disabled={busy || !current || !dirty || mismatch}
            onClick={() => void onSave()}
          >
            {t('common.save')}
          </button>
        </div>
      </div>
    </section>
  )
}

function Localization() {
  const { t } = useTranslation()
  const { config, save } = useConfig()
  const savedLang = config?.app.language ?? 'en'
  const savedTz = config?.app.timezone ?? ''
  const [lang, setLang] = useState(savedLang)
  const [tz, setTz] = useState(savedTz)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const dirty = lang !== savedLang || tz !== savedTz
  useRegisterDirty(dirty)

  // Resync the draft when the saved config changes — after our own save, or an external
  // reload — or the dropdowns and the dirty check drift from the real config (FE-M7).
  useEffect(() => {
    setLang(savedLang)
    setTz(savedTz)
  }, [savedLang, savedTz])

  // "Automatic" first, then the curated list. A valid IANA name outside it (hand-edited
  // config) is kept, so saving cannot silently drop it.
  const tzOptions: Option[] = [
    { value: '', label: t('settings.localization.timezoneAuto') },
    ...(savedTz && !TIMEZONES.includes(savedTz) ? [{ value: savedTz, label: savedTz }] : []),
    ...TIMEZONES.map((z) => ({ value: z, label: z })),
  ]

  async function onSave() {
    if (!config) return
    setBusy(true)
    setErr(null)
    try {
      await save({ ...config, app: { ...config.app, language: lang, timezone: tz } })
      setNote(true)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t('settings.localization.title')}</h2>
      </div>
      <div className="panel-bd stack">
        <div className="frow">
          <div className="field">
            <span className="lab">{t('settings.localization.language')}</span>
            <Dropdown
              value={lang}
              options={LANGS}
              onChange={(v) => {
                setLang(v)
                setNote(false)
                setErr(null)
              }}
            />
          </div>
          <div className="field">
            <span className="lab">{t('settings.localization.timezone')}</span>
            <Dropdown
              value={tz}
              options={tzOptions}
              mono
              onChange={(v) => {
                setTz(v)
                setNote(false)
                setErr(null)
              }}
            />
            <span className="help">{t('settings.localization.timezoneHint')}</span>
          </div>
        </div>
        <div className="save-row">
          {err && <span className="err-note">{err}</span>}
          {note && !dirty && <span className="ok-note">{t('settings.localization.saved')}</span>}
          {dirty && !err && <span className="help">{t('common.unsavedChanges')}</span>}
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
  )
}
