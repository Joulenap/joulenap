import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dropdown, type Option } from '../../components/Dropdown'
import { useConfig } from '../../config/ConfigContext'
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
  const dirty = lang !== savedLang || tz !== savedTz

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
    try {
      await save({ ...config, app: { ...config.app, language: lang, timezone: tz } })
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
        <Dropdown value={lang} options={LANGS} onChange={setLang} />
      </div>

      <div style={{ maxWidth: 320, marginBottom: 8 }}>
        <span style={labelStyle}>{t('settings.localization.timezone')}</span>
        <Dropdown value={tz} options={tzOptions} onChange={setTz} mono />
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
            background: dirty ? c.accent : '#1d232b',
            color: dirty ? c.accentInk : c.textMuted,
            border: dirty ? 'none' : '1px solid #262d35',
            cursor: dirty ? 'pointer' : 'not-allowed',
          }}
        >
          {t('common.save')}
        </button>
        {!dirty && <span style={{ fontSize: 12, color: c.green }}>{t('settings.localization.saved')}</span>}
      </div>
    </div>
  )
}
