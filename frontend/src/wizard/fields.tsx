import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { DeviceError } from '../utils/deviceForm'
import type { CredMode } from '../utils/wizardFlow'

/** Look up the error pinned to a field, if the step has been submitted yet. */
export type ErrorFor = (field: string) => DeviceError | undefined

/** Render a `DeviceError` — either an i18n key this form raised, or verbatim backend prose. */
export function ErrorText({ error }: { error: DeviceError }) {
  const { t } = useTranslation()
  return <span className="err-note">{error.message ?? t(error.key!, error.params)}</span>
}

interface FieldProps {
  id: string
  label: string
  error?: DeviceError
  help?: ReactNode
  children: ReactNode
}

/** One labelled control with its error or help line. The error replaces the help: two lines
 *  competing under one input is how a fixed problem stays on screen. */
export function Field({ id, label, error, help, children }: FieldProps) {
  return (
    <div className={`field${error ? ' err' : ''}`}>
      <label htmlFor={id}>{label}</label>
      {children}
      {error ? <ErrorText error={error} /> : help ? <span className="help">{help}</span> : null}
    </div>
  )
}

interface TextFieldProps {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  error?: DeviceError
  help?: ReactNode
  type?: 'text' | 'password'
  placeholder?: string
  /** The buttons that sit on the input's own row (Detect / Test). */
  trailing?: ReactNode
}

export function TextField({
  id,
  label,
  value,
  onChange,
  error,
  help,
  type = 'text',
  placeholder,
  trailing,
}: TextFieldProps) {
  const input = (
    <input
      id={id}
      type={type}
      className="in-mono"
      value={value}
      placeholder={placeholder}
      autoComplete="off"
      spellCheck={false}
      onChange={(e) => onChange(e.target.value)}
    />
  )
  return (
    <Field id={id} label={label} error={error} help={help}>
      {trailing ? (
        <div className="inline-row">
          {input}
          {trailing}
        </div>
      ) : (
        input
      )}
    </Field>
  )
}

export function NumField({
  id,
  label,
  value,
  onChange,
  error,
}: {
  id: string
  label: string
  value: number
  onChange: (n: number) => void
  error?: DeviceError
}) {
  return (
    <Field id={id} label={label} error={error}>
      <input
        id={id}
        type="number"
        className="in-mono"
        min={1}
        max={65535}
        value={String(value)}
        onChange={(e) => onChange(Math.max(1, Math.floor(Number(e.target.value)) || 0))}
      />
    </Field>
  )
}

export interface CredDraft {
  cred: CredMode
  user: string
  password: string
  tokenId: string
  tokenSecret: string
}

interface CredentialFieldsProps {
  /** Prefixes the input ids so flow A can show PVE and PBS credentials in one dialog. */
  scope: string
  label: string
  help: string
  tokenPlaceholder: string
  draft: CredDraft
  patch: (next: Partial<CredDraft>) => void
  errorFor: ErrorFor
}

/**
 * The credentials radio and the field row it swaps in.
 *
 * Root and token are mutually exclusive rather than merged, because they mean different things:
 * the root password is transient — used once to mint a scoped token and install the SSH key,
 * then dropped — while a pasted token is what gets stored. Showing both at once would invite
 * filling in both and wondering which one was saved.
 *
 * Used four times (PVE and PBS in flow A, PBS in flow B), which is why it is a component and
 * not repeated markup.
 */
export function CredentialFields({
  scope,
  label,
  help,
  tokenPlaceholder,
  draft,
  patch,
  errorFor,
}: CredentialFieldsProps) {
  const { t } = useTranslation()
  const isRoot = draft.cred === 'root'
  return (
    <>
      <div className="field">
        <span className="lab">{label}</span>
        <div className="radio-row">
          <label>
            <input
              type="radio"
              name={`${scope}-cred`}
              checked={isRoot}
              onChange={() => patch({ cred: 'root' })}
            />{' '}
            {t('wizard.cred.root')}
          </label>
          <label>
            <input
              type="radio"
              name={`${scope}-cred`}
              checked={!isRoot}
              onChange={() => patch({ cred: 'token' })}
            />{' '}
            {t('wizard.cred.token')}
          </label>
        </div>
        <span className="help">{help}</span>
      </div>

      {isRoot ? (
        <div className="frow">
          <TextField
            id={`${scope}-user`}
            label={t('wizard.field.user')}
            value={draft.user}
            onChange={(v) => patch({ user: v })}
            error={errorFor('user')}
          />
          <TextField
            id={`${scope}-password`}
            label={t('wizard.field.password')}
            type="password"
            value={draft.password}
            onChange={(v) => patch({ password: v })}
            error={errorFor('password')}
          />
        </div>
      ) : (
        <div className="frow">
          <TextField
            id={`${scope}-token-id`}
            label={t('wizard.field.tokenId')}
            value={draft.tokenId}
            onChange={(v) => patch({ tokenId: v })}
            placeholder={tokenPlaceholder}
            error={errorFor('tokenId')}
          />
          <TextField
            id={`${scope}-token-secret`}
            label={t('wizard.field.tokenSecret')}
            type="password"
            value={draft.tokenSecret}
            onChange={(v) => patch({ tokenSecret: v })}
          />
        </div>
      )}
    </>
  )
}
