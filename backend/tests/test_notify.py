"""M7 notifications: Apprise URL building, message text, routing and the test endpoint."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fakes import make_deps
from fastapi.testclient import TestClient

from app.config import Config
from app.db.models import Run, RunKind, RunStatus, RunStep, RunTrigger, StepName, StepStatus
from app.jobs.backup_cycle import run_backup_cycle
from app.jobs.recorder import RunRecorder
from app.main import create_app
from app.notify import NotificationService
from app.notify.apprise_urls import Channel, build_channels
from app.notify.messages import build_run_message, build_test_message

# --- fake Apprise engine -----------------------------------------------------


class FakeApprise:
    """Records the URLs added and the last notify() payload.

    ``fail_urls`` maps a URL to the WARNING message Apprise would log for it; a URL mapped to
    ``None`` fails silently, like a plugin that returns False without logging. ``raise_urls``
    maps a URL to an exception message, for the plugin that blows up instead of returning.
    """

    def __init__(
        self,
        *,
        add_ok: bool = True,
        notify_ok: bool = True,
        fail_urls: dict[str, str | None] | None = None,
        raise_urls: dict[str, str] | None = None,
    ):
        self.add_ok = add_ok
        self.notify_ok = notify_ok
        self.fail_urls = fail_urls or {}
        self.raise_urls = raise_urls or {}
        self.urls: list[str] = []
        self.payload: tuple[str, str] | None = None

    def add(self, url: str) -> bool:
        if not self.add_ok:
            return False
        self.urls.append(url)
        return True

    def notify(self, title: str = "", body: str = "") -> bool:
        self.payload = (title, body)
        url = self.urls[-1]
        if url in self.raise_urls:
            raise RuntimeError(self.raise_urls[url])
        if url in self.fail_urls:
            message = self.fail_urls[url]
            if message is not None:
                logging.getLogger("apprise").warning(message)
            return False
        return self.notify_ok


def _notifications_config() -> Config:
    cfg = Config()
    n = cfg.notifications
    n.telegram.enabled = True
    n.telegram.bot_token = "123:ABC"
    n.telegram.chat_id = "456"
    n.ntfy.enabled = True
    n.ntfy.url = "https://ntfy.sh"
    n.ntfy.topic = "homelab"
    n.discord.enabled = True
    n.discord.webhook_url = "https://discord.com/api/webhooks/111/tok"
    n.email.enabled = True
    n.email.smtp_host = "smtp.example.com"
    n.email.smtp_port = 587
    n.email.smtp_user = "user@example.com"
    n.email.smtp_password = "p@ss/word"
    n.email.from_addr = "joulenap@example.com"
    n.email.to_addr = "me@example.com"
    n.custom_urls = ["gotify://host/token"]
    return cfg


def _urls(cfg: Config) -> list[str]:
    return [c.url for c in build_channels(cfg.notifications)]


def test_build_channels_labels_every_channel():
    channels = build_channels(_notifications_config().notifications)
    assert [c.name for c in channels] == ["telegram", "ntfy", "email", "discord", "custom #1"]
    assert Channel(name="ntfy", url="ntfys://ntfy.sh/homelab") in channels


def test_custom_urls_are_numbered_from_one():
    cfg = Config()
    cfg.notifications.custom_urls = ["gotify://host/a", "  ", "json://host/b"]
    channels = build_channels(cfg.notifications)
    assert [(c.name, c.url) for c in channels] == [
        ("custom #1", "gotify://host/a"),
        ("custom #2", "json://host/b"),
    ]


# --- URL building ------------------------------------------------------------


def test_build_channels_for_all_channels():
    urls = _urls(_notifications_config())
    assert "tgram://123:ABC/456" in urls
    assert "ntfys://ntfy.sh/homelab" in urls
    assert "discord://111/tok" in urls
    assert "gotify://host/token" in urls
    # email: secure scheme on 587, encoded credentials, from/to as query params
    email = next(u for u in urls if u.startswith("mailtos://"))
    assert "user%40example.com:p%40ss%2Fword@smtp.example.com:587" in email
    assert "mode=starttls" in email


def test_special_chars_in_telegram_and_ntfy_are_percent_encoded():
    # Path-breaking characters in a token/topic must be escaped so the Apprise URL stays
    # well-formed (JN-014) — but a Telegram bot token's structural ``:`` is preserved.
    cfg = _notifications_config()
    cfg.notifications.telegram.bot_token = "123:AB/C"
    cfg.notifications.ntfy.topic = "home lab/#1"
    urls = _urls(cfg)
    assert "tgram://123:AB%2FC/456" in urls
    assert "ntfys://ntfy.sh/home%20lab%2F%231" in urls


def test_disabled_channel_is_skipped():
    cfg = _notifications_config()
    cfg.notifications.telegram.enabled = False
    urls = _urls(cfg)
    assert not any(u.startswith("tgram://") for u in urls)


def test_incomplete_channel_produces_no_url():
    cfg = Config()
    cfg.notifications.telegram.enabled = True  # but no token/chat_id
    cfg.notifications.ntfy.enabled = True
    cfg.notifications.ntfy.url = "http://192.168.1.9"  # but no topic
    assert _urls(cfg) == []


def test_ntfy_http_uses_insecure_scheme():
    cfg = Config()
    cfg.notifications.ntfy.enabled = True
    cfg.notifications.ntfy.url = "http://192.168.1.9:8080"
    cfg.notifications.ntfy.topic = "t"
    assert _urls(cfg) == ["ntfy://192.168.1.9:8080/t"]


# --- messages ----------------------------------------------------------------


def _run(status: RunStatus, *, error: str | None = None) -> Run:
    run = Run(kind=RunKind.CYCLE, trigger=RunTrigger.MANUAL, status=status, error=error)
    run.started_at = datetime(2026, 6, 28, 4, 0, 0, tzinfo=UTC)
    run.finished_at = datetime(2026, 6, 28, 4, 1, 23, tzinfo=UTC)
    return run


def test_run_message_success_english():
    title, body = build_run_message(Config(), _run(RunStatus.SUCCESS))
    assert "succeeded" in title
    assert "1m 23s" in body


def test_run_message_includes_guests_and_datastore():
    from app.connectors.pbs import DatastoreStatus

    run = _run(RunStatus.SUCCESS)
    run.guests_ok = 4
    ds = DatastoreStatus(total=8_000_000_000_000, used=2_000_000_000_000, avail=6_000_000_000_000)
    _title, body = build_run_message(Config(), run, ds)
    assert "Guests: 4" in body
    assert "25.0% used" in body
    assert "5.5 TiB free" in body


def test_run_message_omits_guests_and_datastore_when_absent():
    # No guests_ok and no datastore -> neither line appears (e.g. an aborted run).
    _title, body = build_run_message(Config(), _run(RunStatus.ABORTED))
    assert "Guests" not in body
    assert "Datastore" not in body


def test_run_message_failure_includes_error_and_locale():
    cfg = Config()
    cfg.app.language = "it"
    title, body = build_run_message(cfg, _run(RunStatus.FAILURE, error="vzdump failed"))
    assert "fallito" in title
    assert "vzdump failed" in body


def test_run_message_flags_pbs_left_on_when_poweroff_failed():
    run = _run(RunStatus.SUCCESS)
    run.steps = [RunStep(name=StepName.POWEROFF, status=StepStatus.FAILURE)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" in body


def test_run_message_flags_pbs_left_on_when_poweroff_skipped():
    run = _run(RunStatus.SUCCESS)
    run.steps = [RunStep(name=StepName.POWEROFF, status=StepStatus.SKIPPED)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" in body


def test_run_message_no_pbs_line_when_poweroff_succeeded():
    run = _run(RunStatus.SUCCESS)
    run.steps = [RunStep(name=StepName.POWEROFF, status=StepStatus.SUCCESS)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" not in body


def test_run_message_no_pbs_line_on_failure():
    # A failure notification never gets the success-only "left on" line.
    run = _run(RunStatus.FAILURE, error="boom")
    run.steps = [RunStep(name=StepName.POWEROFF, status=StepStatus.FAILURE)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" not in body


def test_test_message_falls_back_to_english_for_unknown_language():
    cfg = Config()
    cfg.app.language = "xx"
    title, _ = build_test_message(cfg)
    assert "test notification" in title


# --- service dispatch & routing ----------------------------------------------


def test_send_test_dispatches_to_every_channel():
    fake = FakeApprise()
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(_notifications_config())
    assert report.sent is True
    assert report.channels == 5
    assert [r.channel for r in report.results] == [
        "telegram",
        "ntfy",
        "email",
        "discord",
        "custom #1",
    ]
    assert all(r.ok and r.error is None for r in report.results)
    assert fake.payload is not None


def test_send_test_with_no_channels_reports_reason():
    svc = NotificationService(apprise_factory=FakeApprise)
    report = svc.send_test(Config())
    assert report.sent is False
    assert report.reason == "no_channels"
    assert report.results == []


def test_report_attributes_the_failure_to_the_right_channel():
    fake = FakeApprise(
        fail_urls={
            "ntfys://ntfy.sh/homelab": (
                "A Connection error occurred sending ntfy:https://ntfy.sh notification."
            )
        }
    )
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(_notifications_config())

    assert report.sent is False  # one bad channel is enough
    assert report.channels == 5  # but every channel was still attempted
    by_name = {r.channel: r for r in report.results}
    assert by_name["ntfy"].ok is False
    assert "Connection error" in by_name["ntfy"].error
    assert by_name["telegram"].ok is True
    assert by_name["email"].ok is True


def test_failure_without_a_logged_reason_yields_no_error_text():
    # Apprise's log wording is not an API: a silent False must not crash or invent a reason.
    fake = FakeApprise(fail_urls={"ntfys://ntfy.sh/homelab": None})
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(_notifications_config())
    ntfy = next(r for r in report.results if r.channel == "ntfy")
    assert ntfy.ok is False
    assert ntfy.error is None


def test_rejected_url_is_reported_without_sending():
    svc = NotificationService(apprise_factory=lambda: FakeApprise(add_ok=False))
    report = svc.send_test(_notifications_config())
    assert report.sent is False
    assert all(r.ok is False and r.error == "invalid URL" for r in report.results)


def test_a_raising_channel_is_isolated_from_the_others():
    # The spec's guarantee: one broken channel never prevents the others from being tried.
    fake = FakeApprise(raise_urls={"ntfys://ntfy.sh/homelab": "plugin exploded"})
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(_notifications_config())

    by_name = {r.channel: r for r in report.results}
    assert by_name["ntfy"].ok is False
    assert "plugin exploded" in by_name["ntfy"].error
    # every channel after the raising one was still attempted
    assert by_name["email"].ok is True
    assert by_name["discord"].ok is True
    assert by_name["custom #1"].ok is True
    assert report.channels == 5


def test_secrets_never_appear_in_a_channel_error():
    # Apprise sometimes logs the full target URL. Credentials must be scrubbed out of it.
    cfg = _notifications_config()
    fake = FakeApprise(
        fail_urls={
            "tgram://123:ABC/456": "Failed sending to tgram://123:ABC/456",
            "mailtos://user%40example.com:p%40ss%2Fword@smtp.example.com:587"
            "?from=joulenap%40example.com&to=me%40example.com&mode=starttls": (
                "SMTP error for mailtos://user%40example.com:p%40ss%2Fword@smtp.example.com:587"
            ),
        }
    )
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(cfg)

    blob = " ".join(r.error or "" for r in report.results)
    assert "ABC" not in blob
    assert "p@ss/word" not in blob
    assert "p%40ss%2Fword" not in blob
    assert "***" in blob


def test_failed_channels_are_logged_on_a_run(caplog):
    fake = FakeApprise(fail_urls={"ntfys://ntfy.sh/homelab": "boom"})
    svc = NotificationService(apprise_factory=lambda: fake)
    with caplog.at_level(logging.WARNING, logger="app.notify.service"):
        svc.send_run_result(_notifications_config(), _run(RunStatus.SUCCESS))
    assert any("ntfy" in r.message and "boom" in r.message for r in caplog.records)


def test_success_skipped_when_on_success_disabled():
    cfg = _notifications_config()
    cfg.notifications.on_success = False
    svc = NotificationService(apprise_factory=FakeApprise)
    report = svc.send_run_result(cfg, _run(RunStatus.SUCCESS))
    assert report.skipped is True


def test_failure_sent_when_on_failure_enabled():
    fake = FakeApprise()
    cfg = _notifications_config()
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_run_result(cfg, _run(RunStatus.FAILURE, error="boom"))
    assert report.sent is True
    assert "boom" in fake.payload[1]


# --- cycle integration -------------------------------------------------------


def test_backup_cycle_notifies_with_final_run(temp_db):
    captured: list[tuple[RunStatus, bool]] = []
    deps, *_ = make_deps(notify=lambda _c, run, ds: captured.append((run.status, ds is not None)))
    cfg = Config()
    cfg.pve.storage_id = "pbs"
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        run_backup_cycle(cfg, recorder, deps)
    # Success path also captured datastore usage (PBS still awake) for the message.
    assert captured == [(RunStatus.SUCCESS, True)]


def test_notify_failure_does_not_break_cycle(temp_db):
    def boom(_c, _run, _ds=None):
        raise RuntimeError("smtp down")

    deps, *_ = make_deps(notify=boom)
    cfg = Config()
    cfg.pve.storage_id = "pbs"
    with RunRecorder(RunKind.CYCLE, RunTrigger.MANUAL) as recorder:
        run_backup_cycle(cfg, recorder, deps)
        assert recorder.run.status == RunStatus.SUCCESS


# --- endpoint ----------------------------------------------------------------


def test_notify_test_endpoint(temp_config, temp_db, monkeypatch):
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        fake = FakeApprise()
        app.state.notifier = NotificationService(apprise_factory=lambda: fake)
        # configure at least one channel
        raw = app.state.config_store.config.model_dump(mode="python")
        raw["notifications"]["telegram"] = {
            "enabled": True,
            "bot_token": "123:ABC",
            "chat_id": "456",
        }
        app.state.config_store.replace(Config.model_validate(raw))

        res = client.post("/api/notify/test")
        assert res.status_code == 200
        assert res.json() == {"sent": True, "channels": 1}


def test_notify_test_endpoint_no_channels(temp_config, temp_db, monkeypatch):
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        app.state.notifier = NotificationService(apprise_factory=FakeApprise)
        # example config ships telegram enabled; disable everything for this check
        # (keep the current app/auth section so the logged-in session stays valid).
        cfg = app.state.config_store.config.model_copy(deep=True)
        cfg.notifications = Config().notifications
        app.state.config_store.replace(cfg)
        assert client.post("/api/notify/test").status_code == 400
