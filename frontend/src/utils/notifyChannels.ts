// The backend names channels 'telegram' | 'ntfy' | 'email' | 'discord' | 'custom #N'.
// The four built-ins have a translated title in the settings form; custom URLs are
// numbered by the backend and shown as-is.
const TITLE_KEYS: Record<string, string> = {
  telegram: 'settings.notifications.telegramTitle',
  ntfy: 'settings.notifications.ntfyTitle',
  email: 'settings.notifications.emailTitle',
  discord: 'settings.notifications.discordTitle',
}

export function channelLabel(channel: string, t: (key: string) => string): string {
  const key = TITLE_KEYS[channel]
  return key ? t(key) : channel
}
