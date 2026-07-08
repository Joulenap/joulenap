"""M7 notifications: Apprise URL building, message text, routing and the test endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from fakes import make_deps
from fastapi.testclient import TestClient

from app.config import Config
from app.db.models import Run, RunKind, RunStatus, RunStep, RunTrigger, StepName, StepStatus
from app.jobs.backup_cycle import run_backup_cycle
from app.jobs.recorder import RunRecorder
from app.main import create_app
from app.notify import NotificationService
from app.notify.apprise_urls import build_urls
from app.notify.messages import build_run_message, build_test_message

# --- fake Apprise engine -----------------------------------------------------


class FakeApprise:
    """Records the URLs added and the last notify() payload; reports success."""

    def __init__(self, *, add_ok: bool = True, notify_ok: bool = True):
        self.add_ok = add_ok
        self.notify_ok = notify_ok
        self.urls: list[str] = []
        self.payload: tuple[str, str] | None = None

    def add(self, url: str) -> bool:
        if not self.add_ok:
            return False
        self.urls.append(url)
        return True

    def notify(self, title: str = "", body: str = "") -> bool:
        self.payload = (title, body)
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


# --- URL building ------------------------------------------------------------


def test_build_urls_for_all_channels():
    urls = build_urls(_notifications_config().notifications)
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
    urls = build_urls(cfg.notifications)
    assert "tgram://123:AB%2FC/456" in urls
    assert "ntfys://ntfy.sh/home%20lab%2F%231" in urls


def test_disabled_channel_is_skipped():
    cfg = _notifications_config()
    cfg.notifications.telegram.enabled = False
    urls = build_urls(cfg.notifications)
    assert not any(u.startswith("tgram://") for u in urls)


def test_incomplete_channel_produces_no_url():
    cfg = Config()
    cfg.notifications.telegram.enabled = True  # but no token/chat_id
    cfg.notifications.ntfy.enabled = True
    cfg.notifications.ntfy.url = "http://192.168.1.9"  # but no topic
    assert build_urls(cfg.notifications) == []


def test_ntfy_http_uses_insecure_scheme():
    cfg = Config()
    cfg.notifications.ntfy.enabled = True
    cfg.notifications.ntfy.url = "http://192.168.1.9:8080"
    cfg.notifications.ntfy.topic = "t"
    assert build_urls(cfg.notifications) == ["ntfy://192.168.1.9:8080/t"]


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


def test_send_test_dispatches_to_all_channels():
    fake = FakeApprise()
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(_notifications_config())
    assert report.sent is True
    assert report.channels == 5
    assert fake.payload is not None


def test_send_test_with_no_channels_reports_reason():
    svc = NotificationService(apprise_factory=FakeApprise)
    report = svc.send_test(Config())
    assert report.sent is False
    assert report.reason == "no_channels"


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


def test_delivery_failure_is_reported():
    svc = NotificationService(apprise_factory=lambda: FakeApprise(notify_ok=False))
    report = svc.send_test(_notifications_config())
    assert report.sent is False
    assert report.error == "delivery failed"


# --- cycle integration -------------------------------------------------------


def test_backup_cycle_notifies_with_final_run(temp_db):
    captured: list[tuple[RunStatus, bool]] = []
    deps, *_ = make_deps(
        notify=lambda _c, run, ds: captured.append((run.status, ds is not None))
    )
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
        cfg = Config()
        app.state.config_store.replace(cfg)
        assert client.post("/api/notify/test").status_code == 400
