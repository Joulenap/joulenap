import { api } from '../api/client'
import { type DeviceRef, tokenConflictVictims } from '../utils/wizardFlow'

/**
 * The confirm-dialog message for a token-name collision, naming what Joulenap can see break.
 *
 * Asks each registered Proxmox host what storages it has, because the answer is what turned a
 * generic warning into an avoidable outage: a host's PBS storage entry authenticates with the
 * very token about to be replaced, and nobody thinks to look there when backups start 401ing.
 * Best effort — a host that is down must not stop the user deciding, so it degrades to the
 * generic sentence.
 */
export async function tokenConflictMessage(
  host: string,
  t: (key: string, params?: Record<string, unknown>) => string,
  pves: { id: string }[],
  pbss: DeviceRef[],
): Promise<string> {
  const storages: Record<string, { storage: string; host: string }[]> = {}
  await Promise.all(
    pves.map(async (p) => {
      try {
        storages[p.id] = await api.pveStorages(p.id)
      } catch {
        // Unreachable or unauthorised: we simply cannot warn about this one.
      }
    }),
  )
  const victims = tokenConflictVictims(host, pbss, storages)
  const lines = [t('wizard.tokenExists.message')]
  for (const id of victims.devices) lines.push(t('wizard.tokenExists.usedByDevice', { id }))
  for (const s of victims.storages) {
    lines.push(t('wizard.tokenExists.usedByStorage', { storage: s.storage, pve: s.pve }))
  }
  return lines.join('\n\n')
}
