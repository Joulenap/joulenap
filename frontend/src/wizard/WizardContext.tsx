import { createContext, useContext, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import type { WizardStorage } from '../api/types'

// The setup wizard's state lives here, above the Dashboard/Settings view switch, so it
// survives navigation (leaving Settings or switching settings tabs no longer resets it).
// It is held only in memory and the provider unmounts on logout (AppShell unmounts when
// auth drops), so the secrets it carries — root passwords, token secrets — are discarded
// then and never persisted to disk/localStorage. See wizard-ux-followups.

export type Mode = 'rapido' | 'manuale'
export type CardStatus = 'active' | 'done' | 'locked'

export interface Wiz {
  mode: Mode
  status: CardStatus[]
  pveHost: string
  pveUser: string
  pvePass: string
  tokenId: string
  tokenSecret: string
  nodes: string[]
  storages: WizardStorage[]
  node: string
  storage: string
  pbsHost: string
  pbsPort: string
  pbsDatastore: string
  pbsFp: string
  pbsTokenId: string
  pbsTokenSecret: string
  pbsUser: string
  pbsPass: string
  wolIface: string
  wolMac: string
  sshKey: string
  sshKeyLine: string
  sshKeyPath: string
  sshHostKeyType: string
  sshHostKeyB64: string
  sshHostFp: string
  sshHostConfirmed: boolean
  checks: { pve: boolean; pbs: boolean; token: boolean; wol: boolean; ssh: boolean }
}

export function fresh(mode: Mode): Wiz {
  return {
    mode,
    status: ['active', 'locked', 'locked', 'locked', 'locked'],
    pveHost: '',
    pveUser: 'root@pam',
    pvePass: '',
    tokenId: '',
    tokenSecret: '',
    nodes: [],
    storages: [],
    node: '',
    storage: '',
    pbsHost: '',
    pbsPort: '8007',
    pbsDatastore: '',
    pbsFp: '',
    pbsTokenId: '',
    pbsTokenSecret: '',
    pbsUser: 'root',
    pbsPass: '',
    wolIface: '',
    wolMac: '',
    sshKey: '',
    sshKeyLine: '',
    sshKeyPath: '',
    sshHostKeyType: '',
    sshHostKeyB64: '',
    sshHostFp: '',
    sshHostConfirmed: false,
    checks: { pve: false, pbs: false, token: false, wol: false, ssh: false },
  }
}

interface WizardCtx {
  w: Wiz
  setW: Dispatch<SetStateAction<Wiz>>
}

const Ctx = createContext<WizardCtx | null>(null)

export function WizardProvider({ children }: { children: ReactNode }) {
  // Default to token setup: no root password required is the more trustworthy first
  // impression; quick setup (root) is offered as an opt-in accelerator.
  const [w, setW] = useState<Wiz>(() => fresh('manuale'))
  return <Ctx.Provider value={{ w, setW }}>{children}</Ctx.Provider>
}

export function useWizard(): WizardCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useWizard must be used within WizardProvider')
  return v
}
