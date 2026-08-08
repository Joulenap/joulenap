import { Fragment, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { fmtDT } from '../../utils/format'
import {
  countNeverBacked,
  groupGuests,
  guestTypeLabel,
  pbsChip,
  type PveGuests,
} from '../../utils/guestPanel'

/**
 * "Last backup per guest" — read-only. Guest *selection* lives in the route modal now, so
 * this panel answers one question: is everything actually being backed up, and where to.
 *
 * In the bottom grid pair this is the elastic side: its cell contributes no intrinsic
 * height (`.gcell` is `position: relative` with an absolutely-filled panel), so the run
 * history sets the row height and this list scrolls inside it.
 */
export function GuestLastBackup({ groups, loaded }: { groups: PveGuests[]; loaded: boolean }) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const shown = groupGuests(groups, query)
  const never = countNeverBacked(groups)

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>{t('dashboard.lastBackupTitle')}</h2>
        {never > 0 && (
          <span className="nwarn">{t('dashboard.neverBackedUp', { count: never })}</span>
        )}
        <input
          className="gsearch"
          type="text"
          value={query}
          placeholder={t('dashboard.searchGuests')}
          onChange={(e) => setQuery(e.target.value)}
          aria-label={t('dashboard.searchGuests')}
        />
      </div>
      <div className="glist">
        {shown.length === 0 ? (
          <div className="panel-empty">
            {!loaded ? t('common.loading') : query ? t('dashboard.noMatches') : t('dashboard.noGuests')}
          </div>
        ) : (
          shown.map((group) => (
            <Fragment key={group.pve}>
              {/* One divider per PVE — grouping is by source, not a flat list, because a
                  vmid only identifies a guest within its own PVE. */}
              <div className="gdiv">{group.pve}</div>
              {group.error ? (
                <div className="grow">
                  <span className="gerr">{t('dashboard.guestsError')}</span>
                </div>
              ) : (
                group.guests.map((g) => {
                  const chip = pbsChip(g.pbs_ids)
                  const type = guestTypeLabel(g.type)
                  return (
                    <div className="grow" key={g.vmid}>
                      <span className={`gtype ${type === 'VM' ? 'vm' : 'ct'}`}>{type}</span>
                      <span className="gid">{g.vmid}</span>
                      <span className="gname" title={g.name}>
                        {g.name}
                      </span>
                      <span className={`gwhen${g.last_backup ? '' : ' gnever'}`}>
                        {g.last_backup ? fmtDT(new Date(g.last_backup)) : t('dashboard.never')}
                      </span>
                      {chip && (
                        <span className={`pbschip${chip.many ? ' two' : ''}`} title={chip.title}>
                          {chip.label}
                        </span>
                      )}
                    </div>
                  )
                })
              )}
            </Fragment>
          ))
        )}
      </div>
    </section>
  )
}
