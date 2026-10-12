import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { copyToClipboard } from '../utils/clipboard'

/** Copy button that reports the outcome in its own label, as the mockup does. */
export function CopyButton({ text, small }: { text: string; small?: boolean }) {
  const { t } = useTranslation()
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  return (
    <button
      type="button"
      className={`btn${small ? ' btn-sm' : ''}`}
      onClick={async () => {
        const ok = await copyToClipboard(text)
        setState(ok ? 'copied' : 'failed')
        setTimeout(() => setState('idle'), ok ? 1500 : 3000)
      }}
    >
      {state === 'copied'
        ? t('common.copied')
        : state === 'failed'
          ? t('common.copyFailed')
          : t('common.copy')}
    </button>
  )
}
