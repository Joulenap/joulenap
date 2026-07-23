import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../api/client'
import { Dropdown, type Option } from '../../components/Dropdown'
import { useConfig } from '../../config/ConfigContext'
import { useRegisterDirty } from '../../shell/UnsavedGuard'
import { c, labelStyle, panelStyle, primaryBtn } from '../../theme'
import { TIMEZONES } from '../../utils/timezones'

const LANGS = [
  { value: 'en', label: 'English' },
  { value: 'it', label: 'Italiano' },
]

export function Localization() {
  const { t } = useTranslation()
  const { config, save } = useConfig()
  const savedLang = config?.app.language ?? 'en'
  const savedTz = config?.app.timezone ?? ''
  const [lang, setLang] = useState(savedLang)
  const [tz, setTz] = useState(savedTz)
  const [busy, setBusy] = useState(false)
  const [savedNote, setSavedNote] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const dirty = lang !== savedLang || tz !== savedTz
  useRegisterDirty(dirty)

  // Resync the editable draft when the saved config changes — after our own save, or an
  // external reload — mirroring the other settings tabs. Without it the dropdowns and the
  // dirty check drift from the real config (FE-M7).
  useEffect(() => {
    setLang(savedLang)
    setTz(savedTz)
  }, [savedLang, savedTz])

  // Clear the "saved" note (and any error) the moment the user edits either field.
  const onLang = (v: string) => {
    setLang(v)
    setSavedNote(false)
    setErr(null)
  }
  const onTz = (v: string) => {
    setTz(v)
    setSavedNote(false)
    setErr(null)
  }

  // Options: "automatic" first, then the curated list. If the saved value is a valid
  // IANA name outside the list (hand-edited config), keep it so saving doesn't drop it.
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
      setSavedNote(true)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ ...panelStyle, padding: '24px 26px', maxWidth: 580 }}>
      <span style={{ display: 'block', fontSize: 16, fontWeight: 700, marginBottom: 5 }}>
        {t('settings.localization.title')}
      </span>
      <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.5, marginBottom: 24 }}>
        {t('settings.localization.subtitle')}
      </span>

      <div style={{ maxWidth: 320, marginBottom: 24 }}>
        <span style={labelStyle}>{t('settings.localization.language')}</span>
        <Dropdown value={lang} options={LANGS} onChange={onLang} />
      </div>

      <div style={{ maxWidth: 320, marginBottom: 8 }}>
        <span style={labelStyle}>{t('settings.localization.timezone')}</span>
        <Dropdown value={tz} options={tzOptions} onChange={onTz} mono />
      </div>
      <span style={{ display: 'block', fontSize: 12, color: c.textDim, lineHeight: 1.5, marginBottom: 24 }}>
        {t('settings.localization.timezoneHint')}
      </span>

      <div style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
        <button
          onClick={onSave}
          disabled={!dirty || busy}
          style={{
            ...primaryBtn,
            padding: '10px 24px',
            background: dirty ? c.accent : c.btnBg,
            color: dirty ? c.accentInk : c.textMuted,
            border: dirty ? 'none' : `1px solid ${c.btnBorder}`,
            cursor: dirty ? 'pointer' : 'not-allowed',
          }}
        >
          {t('common.save')}
        </button>
        {savedNote && !dirty && <span style={{ fontSize: 12, color: c.green }}>{t('settings.localization.saved')}</span>}
        {err && <span style={{ fontSize: 12, color: c.red }}>{err}</span>}
      </div>
    </div>
  )
}
