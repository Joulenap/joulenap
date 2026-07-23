"""M7 notifications: Apprise URL building, message text, routing and the test endpoint."""

from __future__ import annotations

import logging
import threading
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
from app.notify.messages import (
    build_interrupted_message,
    build_missed_backup_message,
    build_run_message,
    build_test_message,
)

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


def _run(
    status: RunStatus, *, error: str | None = None, kind: RunKind = RunKind.CYCLE
) -> Run:
    run = Run(kind=kind, trigger=RunTrigger.MANUAL, status=status, error=error)
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


def test_run_message_title_names_the_kind_that_ran():
    # A verify or GC cycle must not report itself as a backup (doc-gap #7): a scheduled
    # verify failure used to notify "backup failed".
    verify = build_run_message(Config(), _run(RunStatus.FAILURE, kind=RunKind.VERIFY))[0]
    assert "verification failed" in verify
    assert "backup" not in verify
    gc = build_run_message(Config(), _run(RunStatus.SUCCESS, kind=RunKind.GC))[0]
    assert "garbage collection succeeded" in gc
    assert "backup" not in gc


def test_run_message_kind_titles_are_localized():
    cfg = Config()
    cfg.app.language = "it"
    # Italian agrees in gender with the noun — "verifica fallita", not "fallito".
    assert "verifica fallita" in build_run_message(
        cfg, _run(RunStatus.FAILURE, kind=RunKind.VERIFY)
    )[0]


def test_run_message_unmapped_kind_falls_back_to_the_backup_title():
    # A backup cycle keeps today's wording, and a kind with no block of its own degrades to
    # it rather than raising.
    assert "backup succeeded" in build_run_message(Config(), _run(RunStatus.SUCCESS))[0]
    assert "backup succeeded" in build_run_message(
        Config(), _run(RunStatus.SUCCESS, kind=RunKind.BACKUP)
    )[0]


def _woke() -> RunStep:
    """A completed WAIT step — the PBS came up, so 'left on' hinges only on power-off."""
    return RunStep(name=StepName.WAIT, status=StepStatus.SUCCESS)


def test_run_message_flags_pbs_left_on_when_poweroff_failed():
    run = _run(RunStatus.SUCCESS)
    run.steps = [_woke(), RunStep(name=StepName.POWEROFF, status=StepStatus.FAILURE)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" in body


def test_run_message_flags_pbs_left_on_when_poweroff_skipped():
    run = _run(RunStatus.SUCCESS)
    run.steps = [_woke(), RunStep(name=StepName.POWEROFF, status=StepStatus.SKIPPED)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" in body


def test_run_message_no_pbs_line_when_poweroff_succeeded():
    run = _run(RunStatus.SUCCESS)
    run.steps = [_woke(), RunStep(name=StepName.POWEROFF, status=StepStatus.SUCCESS)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" not in body


def test_run_message_flags_pbs_left_on_when_backup_fails_after_wake():
    # Failure after the PBS woke: no POWEROFF step at all, box is left on for inspection.
    run = _run(RunStatus.FAILURE, error="vzdump failed")
    run.steps = [_woke(), RunStep(name=StepName.BACKUP, status=StepStatus.FAILURE)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" in body


def test_run_message_flags_pbs_left_on_when_aborted_after_wake():
    # An abort after wake (e.g. free-space preflight) also leaves the box on.
    run = _run(RunStatus.ABORTED, error="datastore too full")
    run.steps = [_woke(), RunStep(name=StepName.PRECHECK, status=StepStatus.FAILURE)]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" in body


def test_run_message_no_pbs_line_when_wait_timed_out():
    # Aborted before the PBS came up (WAIT failed): the box never turned on, so no warning.
    run = _run(RunStatus.ABORTED, error="PBS not reachable")
    run.steps = [
        RunStep(name=StepName.WAKE, status=StepStatus.SUCCESS),
        RunStep(name=StepName.WAIT, status=StepStatus.FAILURE),
    ]
    _title, body = build_run_message(Config(), run)
    assert "left powered on" not in body


def test_missed_backup_message_english():
    missed = datetime(2026, 7, 9, 4, 0, tzinfo=UTC)
    last = datetime(2026, 7, 8, 4, 0, tzinfo=UTC)
    nxt = datetime(2026, 7, 12, 4, 0, tzinfo=UTC)
    title, body = build_missed_backup_message(Config(), missed, last, nxt)
    assert "missed scheduled backup" in title
    assert "was offline" in body
    assert "Missed run: 2026-07-09 04:00" in body
    assert "Last backup run: 2026-07-08 04:00" in body
    assert "Next scheduled run: 2026-07-12 04:00" in body


def test_missed_backup_message_localized_italian():
    cfg = Config()
    cfg.app.language = "it"
    title, body = build_missed_backup_message(
        cfg, datetime(2026, 7, 9, 4, 0, tzinfo=UTC), None, None
    )
    assert "mancato" in title
    assert "offline" in body
    # A missing last/next time renders as an em dash rather than crashing.
    assert "Esecuzione mancata: 2026-07-09 04:00" in body


def test_send_missed_backup_dispatches_when_on_failure_enabled():
    cfg = _notifications_config()
    cfg.notifications.on_failure = True
    fake = FakeApprise()
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_missed_backup(
        cfg, datetime(2026, 7, 9, 4, 0, tzinfo=UTC), None, datetime(2026, 7, 12, 4, 0, tzinfo=UTC)
    )
    assert report.sent is True
    assert report.channels == 5
    assert fake.payload is not None and "missed scheduled backup" in fake.payload[0]


def test_send_missed_backup_skipped_when_on_failure_disabled():
    cfg = _notifications_config()
    cfg.notifications.on_failure = False
    svc = NotificationService(apprise_factory=FakeApprise)
    report = svc.send_missed_backup(cfg, datetime(2026, 7, 9, 4, 0, tzinfo=UTC), None, None)
    assert report.sent is False
    assert report.skipped is True
    assert report.reason == "on_failure disabled"


def test_interrupted_message_flags_pbs_left_on_when_it_had_woken():
    # Crashed during backup after the PBS woke: WAIT succeeded, no POWEROFF -> warn.
    run = _run(RunStatus.FAILURE, error="Interrupted — Joulenap restarted")
    run.steps = [
        RunStep(name=StepName.WAIT, status=StepStatus.SUCCESS),
        RunStep(name=StepName.BACKUP, status=StepStatus.FAILURE),
    ]
    title, body = build_interrupted_message(Config(), run)
    assert "interrupted by a restart" in title
    assert "Interrupted — Joulenap restarted" in body
    assert "left powered on" in body


def test_interrupted_message_no_pbs_line_when_it_never_woke():
    # Crashed during WAIT (PBS never came up): no "left on" warning.
    run = _run(RunStatus.FAILURE, error="Interrupted")
    run.steps = [
        RunStep(name=StepName.WAKE, status=StepStatus.SUCCESS),
        RunStep(name=StepName.WAIT, status=StepStatus.FAILURE),
    ]
    _title, body = build_interrupted_message(Config(), run)
    assert "left powered on" not in body


def test_interrupted_message_localized_italian():
    cfg = Config()
    cfg.app.language = "it"
    run = _run(RunStatus.FAILURE)
    run.steps = []
    title, _body = build_interrupted_message(cfg, run)
    assert "interrotta da un riavvio" in title


def test_send_alert_dispatches_when_on_failure_enabled():
    cfg = _notifications_config()
    cfg.notifications.on_failure = True
    fake = FakeApprise()
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_alert(cfg, "a title", "a body")
    assert report.sent is True
    assert report.channels == 5
    assert fake.payload == ("a title", "a body")


def test_send_alert_skipped_when_on_failure_disabled():
    cfg = _notifications_config()
    cfg.notifications.on_failure = False
    svc = NotificationService(apprise_factory=FakeApprise)
    report = svc.send_alert(cfg, "t", "b")
    assert report.sent is False
    assert report.skipped is True
    assert report.reason == "on_failure disabled"


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


def test_a_raising_add_does_not_leak_the_url():
    # ``add`` is the call handed the secret-bearing URL, so an exception from it is the one
    # most likely to quote that URL back at us.
    class RaisingAdd:
        def add(self, url: str) -> bool:
            raise RuntimeError(f"cannot parse {url}")

        def notify(self, title: str = "", body: str = "") -> bool:
            raise AssertionError("notify must not run when add raised")

    svc = NotificationService(apprise_factory=RaisingAdd)
    report = svc.send_test(_notifications_config())

    assert report.sent is False
    assert all(r.ok is False for r in report.results)
    blob = " ".join(r.error or "" for r in report.results)
    assert "ABC" not in blob  # the telegram bot token
    assert "p@ss/word" not in blob  # the smtp password
    assert "***" in blob


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


def test_secret_encoded_with_colon_safe_is_scrubbed():
    # _telegram_url quotes the bot token with quote(token, safe=':') — the colon stays
    # unescaped because it is structural. A token containing '/' therefore appears in the
    # URL as e.g. "123:AB%2FC", a form the raw secret and quote(secret, safe="") both miss.
    cfg = _notifications_config()
    cfg.notifications.telegram.bot_token = "123:AB/C"
    fake = FakeApprise(
        fail_urls={"tgram://123:AB%2FC/456": "Failed sending to tgram://123:AB%2FC/456"}
    )
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(cfg)

    blob = " ".join(r.error or "" for r in report.results)
    assert "AB%2FC" not in blob
    assert "AB/C" not in blob
    assert "***" in blob


def test_ntfy_url_is_scrubbed_from_its_own_error():
    # ntfy has no credential field, so the known-secret list alone would not catch its URL
    # (and topic) if Apprise logged the full target back at us.
    cfg = _notifications_config()
    ntfy_url = "ntfys://ntfy.sh/homelab"
    fake = FakeApprise(fail_urls={ntfy_url: f"A Connection error occurred sending {ntfy_url}"})
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_test(cfg)

    by_name = {r.channel: r for r in report.results}
    assert by_name["ntfy"].ok is False
    assert "ntfy.sh" not in (by_name["ntfy"].error or "")
    assert "homelab" not in (by_name["ntfy"].error or "")
    assert "***" in (by_name["ntfy"].error or "")


def test_log_capture_is_isolated_per_thread():
    # The scheduler runs on a worker thread, so a scheduled send_run_result() can overlap a
    # manual send_test() from the UI. Both attach a handler to the same process-global
    # "apprise" logger; without thread filtering, one send's capture would swallow the
    # other's record and attribute the wrong failure reason to the wrong channel.
    class ForeignNoiseApprise:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def add(self, url: str) -> bool:
            self.urls.append(url)
            return True

        def notify(self, title: str = "", body: str = "") -> bool:
            # Simulate a concurrent send on another thread logging to the same "apprise"
            # logger while this send is in flight. Joined (not slept) so the emission is
            # guaranteed to happen, deterministically, before notify() returns.
            def emit_foreign_record() -> None:
                logging.getLogger("apprise").warning("unrelated failure from another channel")

            other = threading.Thread(target=emit_foreign_record)
            other.start()
            other.join()
            return False  # this channel "fails" but logged nothing of its own

    svc = NotificationService(apprise_factory=ForeignNoiseApprise)
    report = svc.send_test(_notifications_config())

    for result in report.results:
        assert result.error is None or "unrelated failure" not in result.error


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


def test_notify_test_endpoint_reports_each_channel(temp_config, temp_db, monkeypatch):
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        fake = FakeApprise(fail_urls={"ntfys://ntfy.sh/t": "Failed to resolve 'ntfy.sh'"})
        app.state.notifier = NotificationService(apprise_factory=lambda: fake)
        raw = app.state.config_store.config.model_dump(mode="python")
        raw["notifications"]["telegram"] = {
            "enabled": True,
            "bot_token": "123:ABC",
            "chat_id": "456",
        }
        raw["notifications"]["ntfy"] = {"enabled": True, "url": "https://ntfy.sh", "topic": "t"}
        app.state.config_store.replace(Config.model_validate(raw))

        res = client.post("/api/notify/test")
        assert res.status_code == 200
        assert res.json() == {
            "channels": [
                {"channel": "telegram", "ok": True, "error": None},
                {"channel": "ntfy", "ok": False, "error": "Failed to resolve 'ntfy.sh'"},
            ]
        }


def test_notify_test_endpoint_no_channels_is_an_empty_report(temp_config, temp_db, monkeypatch):
    # Nothing configured is not an error: the request succeeded, there was just nothing to do.
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

        res = client.post("/api/notify/test")
        assert res.status_code == 200
        assert res.json() == {"channels": []}
