import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Dropdown, type Option } from '../components/Dropdown'
import { c, inputStyle, labelStyle, primaryBtn } from '../theme'
import { TIMEZONES, detectTimezone } from '../utils/timezones'

export function Login() {
  const { t } = useTranslation()
  const { setupNeeded, login, setup } = useAuth()
  const register = setupNeeded

  const [user, setUser] = useState('')
  const [pass, setPass] = useState('')
  const [pass2, setPass2] = useState('')
  // Default to the browser's timezone so most users just confirm; fall back to UTC.
  const [tz, setTz] = useState(() => detectTimezone() || 'UTC')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // The detected zone first (added if it's not already in the curated list), then the list.
  const tzOptions: Option[] = [
    ...(tz && !TIMEZONES.includes(tz) ? [{ value: tz, label: tz }] : []),
    ...TIMEZONES.map((z) => ({ value: z, label: z })),
  ]

  async function submit() {
    setError('')
    if (register) {
      if (user.trim().length < 3) return setError(t('auth.errors.userShort'))
      if (pass.length < 4) return setError(t('auth.errors.passShort'))
      if (pass !== pass2) return setError(t('auth.errors.mismatch'))
    }
    setBusy(true)
    try {
      if (register) await setup(user.trim(), pass, tz)
      else await login(user.trim(), pass)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setError(t('auth.errors.invalid'))
      else setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') submit()
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <div style={{ width: 384, maxWidth: '100%' }}>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 14,
            marginBottom: 26,
          }}
        >
          <img src="/assets/joulenap-icon.svg" alt="Joulenap" style={{ height: 62, width: 62 }} />
          <img
            src="/assets/joulenap-wordmark.svg"
            alt="Joulenap"
            style={{ height: 30, filter: 'brightness(0) invert(1)' }}
          />
        </div>
        <div
          style={{
            background: c.panel,
            border: `1px solid ${c.border}`,
            borderRadius: 14,
            padding: 26,
          }}
        >
          <span style={{ display: 'block', fontSize: 17, fontWeight: 700, marginBottom: 5 }}>
            {register ? t('auth.registerTitle') : t('auth.signInTitle')}
          </span>
          <span
            style={{
              display: 'block',
              fontSize: 13,
              color: c.textDim,
              lineHeight: 1.5,
              marginBottom: 22,
            }}
          >
            {register ? t('auth.registerSubtitle') : t('auth.signInSubtitle')}
          </span>

          <label style={{ display: 'block', marginBottom: 14 }}>
            <span style={labelStyle}>{t('auth.username')}</span>
            <input
              value={user}
              onChange={(e) => {
                setUser(e.target.value)
                setError('')
              }}
              onKeyDown={onKey}
              autoComplete="off"
              placeholder="admin"
              style={inputStyle}
            />
          </label>

          <label style={{ display: 'block', marginBottom: 14 }}>
            <span style={labelStyle}>{t('auth.password')}</span>
            <input
              type="password"
              value={pass}
              onChange={(e) => {
                setPass(e.target.value)
                setError('')
              }}
              onKeyDown={onKey}
              placeholder="••••••••"
              style={inputStyle}
            />
          </label>

          {register && (
            <label style={{ display: 'block', marginBottom: 14 }}>
              <span style={labelStyle}>{t('auth.confirmPassword')}</span>
              <input
                type="password"
                value={pass2}
                onChange={(e) => {
                  setPass2(e.target.value)
                  setError('')
                }}
                onKeyDown={onKey}
                placeholder="••••••••"
                style={inputStyle}
              />
            </label>
          )}

          {register && (
            <div style={{ marginBottom: 14 }}>
              <span style={labelStyle}>{t('auth.timezone')}</span>
              <Dropdown value={tz} options={tzOptions} onChange={setTz} mono />
              <span
                style={{
                  display: 'block',
                  fontSize: 11,
                  color: c.textDim,
                  lineHeight: 1.5,
                  marginTop: 6,
                }}
              >
                {t('auth.timezoneHint')}
              </span>
            </div>
          )}

          {error && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                background: 'rgba(229,103,91,.12)',
                border: '1px solid #5e3330',
                borderRadius: 7,
                padding: '9px 12px',
                marginBottom: 14,
                fontSize: 12,
                color: c.red,
              }}
            >
              ⚠ {error}
            </div>
          )}

          <button
            onClick={submit}
            disabled={busy}
            style={{ ...primaryBtn, width: '100%', padding: 12, marginTop: 6, fontSize: 14 }}
          >
            {register ? t('auth.registerButton') : t('auth.signInButton')}
          </button>
        </div>
        <div
          style={{
            textAlign: 'center',
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 11,
            color: '#4d555f',
            marginTop: 18,
          }}
        >
          {t('auth.brand')}
        </div>
      </div>
    </div>
  )
}
