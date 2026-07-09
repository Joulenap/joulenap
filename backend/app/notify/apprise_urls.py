"""Turn the friendly notification config into Apprise URLs.

The UI offers simple forms for the common channels (Telegram, ntfy, email, Discord);
under the hood every channel — plus the free-form ``custom_urls`` list — becomes an
Apprise URL so a single engine (apprise) does the delivery. A channel only
contributes a URL when it is enabled *and* has the fields it needs, so a half-filled
form silently produces nothing rather than a broken URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlparse

from ..config import (
    DiscordConfig,
    EmailConfig,
    NotificationsConfig,
    NtfyConfig,
    TelegramConfig,
)


@dataclass(frozen=True)
class Channel:
    """One delivery target: a display name for the report, and the Apprise URL to send with.

    The URL carries the channel's secrets, so it stays inside the backend — only ``name``
    is ever shown to the user.
    """

    name: str
    url: str


def build_channels(n: NotificationsConfig) -> list[Channel]:
    """All enabled channels as ``(name, url)`` pairs, followed by any custom URLs."""
    channels: list[Channel] = []
    for name, builder, channel in (
        ("telegram", _telegram_url, n.telegram),
        ("ntfy", _ntfy_url, n.ntfy),
        ("email", _email_url, n.email),
        ("discord", _discord_url, n.discord),
    ):
        url = builder(channel) if channel.enabled else None
        if url:
            channels.append(Channel(name=name, url=url))
    for i, url in enumerate((u.strip() for u in n.custom_urls if u.strip()), start=1):
        channels.append(Channel(name=f"custom #{i}", url=url))
    return channels


def _telegram_url(t: TelegramConfig) -> str | None:
    if not (t.bot_token and t.chat_id):
        return None
    # A Telegram bot token is structurally ``<id>:<secret>`` — the colon is required by
    # Apprise's parser, so keep it unescaped; only guard the path-breaking characters.
    return f"tgram://{quote(t.bot_token, safe=':')}/{quote(t.chat_id, safe='')}"


def _ntfy_url(n: NtfyConfig) -> str | None:
    if not (n.url and n.topic):
        return None
    parsed = urlparse(n.url if "://" in n.url else f"http://{n.url}")
    if not parsed.netloc:
        return None
    scheme = "ntfys" if parsed.scheme == "https" else "ntfy"
    return f"{scheme}://{parsed.netloc}/{quote(n.topic, safe='')}"


def _email_url(e: EmailConfig) -> str | None:
    if not (e.smtp_host and e.from_addr and e.to_addr):
        return None
    auth = ""
    if e.smtp_user:
        auth = f"{quote(e.smtp_user, safe='')}:{quote(e.smtp_password, safe='')}@"
    # 587 = STARTTLS, 465 = implicit TLS; anything else is treated as plain SMTP.
    if e.smtp_port == 465:
        scheme, mode = "mailtos", "ssl"
    elif e.smtp_port == 587:
        scheme, mode = "mailtos", "starttls"
    else:
        scheme, mode = "mailto", None
    params = {"from": e.from_addr, "to": e.to_addr}
    if mode:
        params["mode"] = mode
    return f"{scheme}://{auth}{e.smtp_host}:{e.smtp_port}?{urlencode(params)}"


def _discord_url(d: DiscordConfig) -> str | None:
    raw = d.webhook_url.strip()
    if not raw:
        return None
    if raw.startswith("discord://"):
        return raw
    # Convert a Discord webhook URL (…/api/webhooks/{id}/{token}) to discord://{id}/{token}.
    parts = [p for p in urlparse(raw).path.split("/") if p]
    if len(parts) < 2:
        return None
    return f"discord://{parts[-2]}/{parts[-1]}"
