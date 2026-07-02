// Curated IANA zones so users pick from a list rather than typing a name that could
// silently fall back to UTC on a typo. Shared by the first-run screen and the
// Localization settings panel. An empty value means "use the TZ env var, then UTC"
// (see backend core/scheduler.resolve_timezone).
export const TIMEZONES = [
  'UTC',
  'Europe/London', 'Europe/Dublin', 'Europe/Lisbon', 'Europe/Madrid', 'Europe/Paris',
  'Europe/Brussels', 'Europe/Amsterdam', 'Europe/Berlin', 'Europe/Zurich', 'Europe/Rome',
  'Europe/Vienna', 'Europe/Prague', 'Europe/Warsaw', 'Europe/Stockholm', 'Europe/Helsinki',
  'Europe/Athens', 'Europe/Istanbul', 'Europe/Moscow',
  'America/New_York', 'America/Toronto', 'America/Chicago', 'America/Mexico_City',
  'America/Denver', 'America/Phoenix', 'America/Los_Angeles', 'America/Vancouver',
  'America/Bogota', 'America/Sao_Paulo', 'America/Santiago', 'America/Argentina/Buenos_Aires',
  'Asia/Jerusalem', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Bangkok', 'Asia/Shanghai',
  'Asia/Hong_Kong', 'Asia/Singapore', 'Asia/Tokyo', 'Asia/Seoul',
  'Australia/Perth', 'Australia/Sydney', 'Pacific/Auckland',
  'Africa/Cairo', 'Africa/Lagos', 'Africa/Nairobi', 'Africa/Johannesburg',
]

/** The browser's current IANA timezone (e.g. "Europe/Rome"), or "" if unavailable. */
export function detectTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || ''
  } catch {
    return ''
  }
}
