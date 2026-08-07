import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ConfirmModal, type ConfirmState } from '../components/ConfirmModal'

// The "unsaved changes" guard (FE-M2). Editing surfaces register their dirty state; in-app
// navigation routes through `guard()`, which asks before discarding a dirty draft instead of
// unmounting it silently. The setup wizard doesn't participate — its state already survives
// navigation via WizardProvider.
//
// The registry is keyed, not single-slot: M11 put two independently-saved forms on one tab
// (Account's credentials + localization, Advanced's application card + the YAML editor), and
// with one slot the second to mount silently unregistered the first — its edits were then
// thrown away with no prompt. Any registrant being dirty is enough.
type DirtyGetter = () => boolean

interface UnsavedGuardValue {
  registerDirty: (key: string, getter: DirtyGetter | null) => void
  guard: (action: () => void) => void
}

const Ctx = createContext<UnsavedGuardValue | null>(null)

export function UnsavedGuardProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const dirtyRef = useRef(new Map<string, DirtyGetter>())
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  const registerDirty = useCallback((key: string, getter: DirtyGetter | null) => {
    if (getter) dirtyRef.current.set(key, getter)
    else dirtyRef.current.delete(key)
  }, [])

  const isDirty = useCallback(() => [...dirtyRef.current.values()].some((g) => g()), [])

  const guard = useCallback(
    (action: () => void) => {
      if (!isDirty()) {
        action()
        return
      }
      setConfirm({
        title: t('common.unsaved.title'),
        message: t('common.unsaved.body'),
        confirmLabel: t('common.unsaved.confirm'),
        danger: true,
        icon: '⚠',
        onConfirm: () => {
          // The surfaces we're leaving are about to unmount; drop their registrations now so
          // a stale getter can't mis-guard the next navigation before their cleanup runs.
          dirtyRef.current.clear()
          action()
        },
      })
    },
    [isDirty, t],
  )

  // Also warn on browser tab-close / refresh while a draft is dirty. This is a native browser
  // prompt (text is not localizable / not shown by modern browsers), unlike the in-app modal.
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!isDirty()) return
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isDirty])

  return (
    <Ctx.Provider value={{ registerDirty, guard }}>
      {children}
      <ConfirmModal state={confirm} onCancel={() => setConfirm(null)} />
    </Ctx.Provider>
  )
}

export function useUnsavedGuard(): UnsavedGuardValue {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useUnsavedGuard must be used within UnsavedGuardProvider')
  return ctx
}

// Publish this surface's dirty state to the guard for as long as it's mounted. The registered
// getter reads a ref so the value stays current without re-registering on every keystroke, and
// the registration is cleared on unmount. `useId` gives each caller its own slot, so two forms
// on the same tab don't overwrite each other.
export function useRegisterDirty(dirty: boolean): void {
  const { registerDirty } = useUnsavedGuard()
  const key = useId()
  const ref = useRef(dirty)
  ref.current = dirty
  useEffect(() => {
    registerDirty(key, () => ref.current)
    return () => registerDirty(key, null)
  }, [registerDirty, key])
}
