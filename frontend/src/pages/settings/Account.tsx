import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import { c, ghostBtn, inputStyle, labelStyle, panelStyle, primaryBtn } from '../../theme'

export function Account() {
  const { t } = useTranslation()
  const { username, setUsername } = useAuth()

  const [editing, setEditing] = useState(false)
  const [user, setUser] = useState(username ?? '')
  const [current, setCurrent] = useState('')
  const [pass, setPass] = useState('')
  const [busy, setBusy] = useState(false)
  const [savedNote, setSavedNote] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  function startEdit() {
    setUser(username ?? '')
    setCurrent('')
    setPass('')
    setSavedNote(false)
    setErr(null)
    setEditing(true)
  }

  async function onSave() {
    setBusy(true)
    setErr(null)
    try {
      const u = await api.updateAccount(current, user.trim() || (username ?? ''), pass || undefined)
      setUsername(u.username)
      setEditing(false)
      setSavedNote(true)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t('common.saveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const readField = (label: string, value: string, masked = false) => (
    <div>
      <span style={labelStyle}>{label}</span>
      <div
        style={{
          background: c.inputBg,
          border: `1px solid ${c.border}`,
          borderRadius: 7,
          color: c.textMid,
          padding: '10px 12px',
          fontSize: 14,
          fontFamily: masked ? "'IBM Plex Mono', monospace" : undefined,
          letterSpacing: masked ? '.18em' : undefined,
        }}
      >
        {value}
      </div>
    </div>
  )

  return (
    <div style={{ ...panelStyle, padding: '24px 26px', maxWidth: 580 }}>
      <span style={{ display: 'block', fontSize: 16, fontWeight: 700, marginBottom: 5 }}>
        {t('settings.account.title')}
      </span>
      <span style={{ display: 'block', fontSize: 13, color: c.textDim, lineHeight: 1.5, marginBottom: 24 }}>
        {t('settings.account.subtitle')}
      </span>

      {!editing ? (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 360, marginBottom: 24 }}>
            {readField(t('settings.account.username'), username ?? '—')}
            {readField(t('settings.account.password'), '••••••••', true)}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
            <button onClick={startEdit} style={{ ...ghostBtn, padding: '10px 22px' }}>
              {t('common.edit')}
            </button>
            {savedNote && <span style={{ fontSize: 12, color: c.green }}>{t('settings.account.saved')}</span>}
          </div>
        </>
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 360, marginBottom: 22 }}>
            <label style={{ display: 'block' }}>
              <span style={labelStyle}>{t('settings.account.currentPassword')}</span>
              <input
                type="password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                autoComplete="current-password"
                style={inputStyle}
              />
            </label>
            <label style={{ display: 'block' }}>
              <span style={labelStyle}>{t('settings.account.username')}</span>
              <input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" style={inputStyle} />
            </label>
            <label style={{ display: 'block' }}>
              <span style={labelStyle}>{t('settings.account.newPassword')}</span>
              <input
                type="password"
                value={pass}
                onChange={(e) => setPass(e.target.value)}
                placeholder={t('settings.account.keepPlaceholder')}
                style={inputStyle}
              />
            </label>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button onClick={onSave} disabled={busy || !current} style={{ ...primaryBtn, padding: '10px 24px' }}>
              {t('common.save')}
            </button>
            <button onClick={() => setEditing(false)} style={{ ...ghostBtn, padding: '10px 20px' }}>
              {t('common.cancel')}
            </button>
            {err && <span style={{ fontSize: 12, color: c.red }}>{err}</span>}
          </div>
        </>
      )}
    </div>
  )
}
