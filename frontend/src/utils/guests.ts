/**
 * i18n key for a blocking validation error on the guest selection, or null if valid.
 *
 * Only Selective (`include`) mode with an empty list is invalid: it saves a schedule
 * that wakes the PBS and aborts every run ("No guests selected") without ever backing
 * anything up. `general` (all) and `exclude` (all-except, read-only) are always valid —
 * an empty exclude list means "back up everything".
 */
export function guestsSelectionError(
  mode: 'general' | 'selective' | 'exclude',
  selectedCount: number,
): string | null {
  if (mode === 'selective' && selectedCount === 0) return 'dashboard.noGuestsSelected'
  return null
}
