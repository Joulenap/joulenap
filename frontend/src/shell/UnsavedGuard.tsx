import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ConfirmModal, type ConfirmState } from '../components/ConfirmModal'

// A single-slot "unsaved changes" guard (FE-M2). The one editing surface that's mounted at a
// time (Dashboard scheduler, or the active Settings tab — never both) registers its dirty
// state; in-app navigation routes through `guard()`, which asks before discarding a dirty
// draft instead of unmounting it silently. The setup wizard doesn't participate — its state
// already survives navigation via WizardProvider.
type DirtyGetter = () => boolean

interface UnsavedGuardValue {
  registerDirty: (getter: DirtyGetter | null) => void
  guard: (action: () => void) => void
}

const Ctx = createContext<UnsavedGuardValue | null>(null)

export function UnsavedGuardProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const dirtyRef = useRef<DirtyGetter | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)

  const registerDirty = useCallback((getter: DirtyGetter | null) => {
    dirtyRef.current = getter
  }, [])

  const isDirty = useCallback(() => !!dirtyRef.current?.(), [])

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
          // The surface we're leaving is about to unmount; drop its registration now so a
          // stale getter can't mis-guard the next navigation before its cleanup runs.
          dirtyRef.current = null
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
// the registration is cleared on unmount.
export function useRegisterDirty(dirty: boolean): void {
  const { registerDirty } = useUnsavedGuard()
  const ref = useRef(dirty)
  ref.current = dirty
  useEffect(() => {
    registerDirty(() => ref.current)
    return () => registerDirty(null)
  }, [registerDirty])
}
