"""M7 notifications: Apprise URL building, message text, routing and the test endpoint."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from fakes import make_deps
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Config, Route
from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus, RunStep, RunTrigger, StepName, StepStatus
from app.db.startup import _INTERRUPTED_STEP
from app.jobs.lease import ReleaseOutcome
from app.jobs.route_cycle import monitor_detail
from app.main import create_app
from app.notify import NotificationService
from app.notify.apprise_urls import Channel, build_channels
from app.notify.messages import (
    _DETAILS,
    _ERRORS,
    _KIND_LABEL,
    _MESSAGES,
    GuestSummary,
    RunContext,
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
        self.body_format: str | None = None

    def add(self, url: str) -> bool:
        if not self.add_ok:
            return False
        self.urls.append(url)
        return True

    def notify(self, title: str = "", body: str = "", body_format: str = "") -> bool:
        self.payload = (title, body)
        self.body_format = body_format
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
    status: RunStatus,
    *,
    error: str | None = None,
    error_key: str | None = None,
    error_params: str | None = None,
    kind: RunKind = RunKind.CYCLE,
    trigger: RunTrigger = RunTrigger.MANUAL,
) -> Run:
    run = Run(
        kind=kind,
        trigger=trigger,
        status=status,
        error=error,
        error_key=error_key,
        error_params=error_params,
    )
    run.id = 128
    run.started_at = datetime(2026, 6, 28, 4, 0, 0, tzinfo=UTC)
    run.finished_at = datetime(2026, 6, 28, 4, 1, 23, tzinfo=UTC)
    return run


def _route(route_id: str = "nightly", name: str = "Nightly", **overrides) -> Route:
    return Route.model_validate(
        {"id": route_id, "name": name, "kind": "verify", "target": "pbs-01", **overrides}
    )


def _msg(config, run, datastore=None, guests=None, next_at=None, route=None, left_on=()):
    """``build_run_message`` with the old positional tail, so these tests stay readable.

    The production seam is a single :class:`RunContext`; spelling that out at ~25 call sites
    would say nothing the field names don't.
    """
    return build_run_message(
        RunContext(
            config=config,
            run=run,
            route=route,
            datastore=datastore,
            guests=guests,
            next_at=next_at,
            left_on=list(left_on),
        )
    )


def _send(svc, config, run, **kwargs):
    return svc.send_run_result(RunContext(config=config, run=run, **kwargs))


def _step(name: StepName, status: StepStatus, seconds: int, label: str | None = None) -> RunStep:
    """A finished step that took ``seconds``, for the duration breakdown.

    ``label`` produces the ``backup:pve-alpha`` / ``poweroff:pbs-02`` form a multi-device run
    records — which is what the route cycles actually write.
    """
    step = RunStep(name=f"{name.value}:{label}" if label else name, status=status)
    step.started_at = datetime(2026, 6, 28, 4, 0, 0, tzinfo=UTC)
    step.finished_at = step.started_at + timedelta(seconds=seconds)
    return step


def test_run_message_success_english():
    title, body = _msg(Config(), _run(RunStatus.SUCCESS))
    assert "succeeded" in title
    assert "1m 23s" in body


def test_run_message_includes_guests_and_datastore():
    from app.connectors.pbs import DatastoreStatus

    run = _run(RunStatus.SUCCESS)
    ds = DatastoreStatus(total=8_000_000_000_000, used=2_000_000_000_000, avail=6_000_000_000_000)
    _title, body = _msg(Config(), run, ds, GuestSummary(total=4, ok=4))
    assert "Guests: 4/4" in body
    assert "25.0% used" in body
    assert "5.5 TiB free" in body


def test_run_message_omits_guests_and_datastore_when_absent():
    # No guest summary and no datastore -> neither line appears (e.g. an aborted run).
    _title, body = _msg(Config(), _run(RunStatus.ABORTED))
    assert "Guests" not in body
    assert "Datastore" not in body


def test_run_message_names_the_guests_that_failed():
    """A single guest failing takes the whole vzdump task down, so the run reads FAILURE with
    no hint of which guest broke. The tally is the only place that detail exists."""
    _title, body = _msg(Config(),
        _run(RunStatus.FAILURE, error="vzdump failed"),
        None,
        GuestSummary(total=14, ok=12, failed=["web01", "db02"]),
    )
    assert "Guests: 12/14 (failed: web01, db02)" in body


def test_run_message_omits_the_guest_line_when_nothing_was_selected():
    # total == 0 means the cycle never got as far as picking guests: "0/0" would read like a
    # result when it is really an absence.
    _title, body = _msg(Config(), _run(RunStatus.ABORTED), None, GuestSummary())
    assert "Guests" not in body


def test_run_message_field_order():
    """The order is the spec — asserting the lines individually wouldn't catch a reshuffle."""
    from app.connectors.pbs import DatastoreStatus

    run = _run(RunStatus.FAILURE, error="boom", trigger=RunTrigger.SCHEDULED)
    run.steps = [_woke(), RunStep(name=StepName.BACKUP, status=StepStatus.FAILURE)]
    ds = DatastoreStatus(total=8_000_000_000_000, used=2_000_000_000_000, avail=6_000_000_000_000)
    _title, body = _msg(Config(),
        run,
        ds,
        GuestSummary(total=2, ok=1, failed=["web01"]),
        datetime(2026, 6, 29, 4, 0, tzinfo=UTC),
        left_on=["pbs-01"],
    )
    assert [line.split(":")[0] for line in body.splitlines()] == [
        "Trigger",
        "Duration",
        "Guests",
        "Datastore",
        "Error",
        "⚠️ PBS left powered on — check it",
        "Next scheduled run",
        "Run #128",
    ]


def test_run_message_duration_breaks_down_the_work_phases():
    """Where the time actually went. Wake/wait/power-off stay out: near-constant overhead
    that would only crowd the line."""
    run = _run(RunStatus.SUCCESS)
    run.steps = [
        _step(StepName.WAIT, StepStatus.SUCCESS, 40),
        # Labelled, because that is the only shape a backup route can produce: one step per
        # source PVE. An equality lookup matched nothing here and dropped the phase entirely.
        _step(StepName.BACKUP, StepStatus.SUCCESS, 70, label="pve-alpha"),
        RunStep(name=StepName.GC, status=StepStatus.SKIPPED),
        _step(StepName.POWEROFF, StepStatus.SUCCESS, 9),
    ]
    _title, body = _msg(Config(), run)
    assert "Duration: 1m 23s (backup 1m 10s)" in body


def test_run_message_sums_one_backup_phase_across_several_sources():
    """A fan-in route records ``backup:pve-alpha`` and ``backup:pve-beta``; the line should
    read one ``backup`` slice, not two entries and not just the last one."""
    run = _run(RunStatus.SUCCESS)
    run.steps = [
        _step(StepName.WAIT, StepStatus.SUCCESS, 40),
        _step(StepName.BACKUP, StepStatus.SUCCESS, 70, label="pve-alpha"),
        _step(StepName.BACKUP, StepStatus.SUCCESS, 50, label="pve-beta"),
        _step(StepName.GC, StepStatus.SUCCESS, 66),
        _step(StepName.POWEROFF, StepStatus.SUCCESS, 9),
    ]
    _title, body = _msg(Config(), run)
    assert "(backup 2m 0s · GC 1m 6s)" in body


def test_run_message_trigger_and_next_run_are_localized():
    cfg = Config()
    cfg.app.language = "it"
    _title, body = _msg(cfg,
        _run(RunStatus.SUCCESS, trigger=RunTrigger.SCHEDULED),
        None,
        None,
        datetime(2026, 6, 29, 4, 0, tzinfo=UTC),
    )
    assert body.startswith("Avvio: pianificato")
    assert "Prossima esecuzione pianificata: 2026-06-29 04:00" in body
    assert body.endswith("Run #128")


def test_run_message_omits_next_run_when_no_schedule_is_armed():
    # A disabled/invalid schedule leaves no armed job: better no line than a "—" placeholder,
    # which reads like an error in an otherwise-fine success push.
    _title, body = _msg(Config(), _run(RunStatus.SUCCESS))
    assert "Next scheduled run" not in body


def test_run_message_failure_includes_error_and_locale():
    cfg = Config()
    cfg.app.language = "it"
    title, body = _msg(cfg, _run(RunStatus.FAILURE, error="vzdump failed"))
    assert "fallito" in title
    assert "vzdump failed" in body


def test_run_message_title_names_the_kind_that_ran():
    # A verify or GC cycle must not report itself as a backup (doc-gap #7): a scheduled
    # verify failure used to notify "backup failed".
    verify = _msg(Config(), _run(RunStatus.FAILURE, kind=RunKind.VERIFY))[0]
    assert "verification failed" in verify
    assert "backup" not in verify
    gc = _msg(Config(), _run(RunStatus.SUCCESS, kind=RunKind.GC))[0]
    assert "garbage collection succeeded" in gc
    assert "backup" not in gc


def test_run_message_kind_titles_are_localized():
    cfg = Config()
    cfg.app.language = "it"
    # Italian agrees in gender with the noun — "verifica fallita", not "fallito".
    assert "verifica fallita" in _msg(cfg, _run(RunStatus.FAILURE, kind=RunKind.VERIFY)
    )[0]


def test_run_message_unmapped_kind_falls_back_to_the_backup_title():
    # A backup cycle keeps today's wording, and a kind with no block of its own degrades to
    # it rather than raising.
    assert "backup succeeded" in _msg(Config(), _run(RunStatus.SUCCESS))[0]
    assert "backup succeeded" in _msg(Config(), _run(RunStatus.SUCCESS, kind=RunKind.BACKUP)
    )[0]


def _woke() -> RunStep:
    """A completed WAIT step — the PBS came up, so 'left on' hinges only on power-off."""
    return RunStep(name=StepName.WAIT, status=StepStatus.SUCCESS)


def test_run_message_flags_the_boxes_the_run_left_awake():
    # A finished run doesn't guess from its own timeline: JobService hands it the ids of the
    # boxes still burning power (RunContext.left_on), because only the lease knows whether
    # "not powered off" meant an always-on box, one another run still holds, or a real
    # left-on. Which reason applies is decided (and tested) in tests/test_queue.py.
    run = _run(RunStatus.FAILURE, error="vzdump failed")
    run.steps = [_woke(), RunStep(name=StepName.BACKUP, status=StepStatus.FAILURE)]
    _title, body = _msg(Config(), run, left_on=["pbs-01"])
    assert "left powered on" in body


def test_run_message_no_pbs_line_when_nothing_was_left_awake():
    # A SKIPPED power-off is not evidence on its own — an always-on PBS records exactly
    # this on every successful run, and used to be warned about every night.
    run = _run(RunStatus.SUCCESS)
    run.steps = [_woke(), RunStep(name=StepName.POWEROFF, status=StepStatus.SKIPPED)]
    _title, body = _msg(Config(), run, left_on=[])
    assert "left powered on" not in body


def test_missed_backup_message_english():
    missed = datetime(2026, 7, 9, 4, 0, tzinfo=UTC)
    last = datetime(2026, 7, 8, 4, 0, tzinfo=UTC)
    nxt = datetime(2026, 7, 12, 4, 0, tzinfo=UTC)
    title, body = build_missed_backup_message(Config(), _route(), missed, last, nxt)
    assert "missed scheduled run" in title
    assert "Route: Nightly (verify)" in body  # which one, now that every route has its own schedule
    assert "was offline" in body
    assert "Missed run: 2026-07-09 04:00" in body
    assert "Last run: 2026-07-08 04:00" in body
    assert "Next scheduled run: 2026-07-12 04:00" in body


def test_missed_backup_message_localized_italian():
    cfg = Config()
    cfg.app.language = "it"
    title, body = build_missed_backup_message(
        cfg, _route(), datetime(2026, 7, 9, 4, 0, tzinfo=UTC), None, None
    )
    # "esecuzione ... mancata", feminine — the agreement the pack spells out per string
    # rather than templating a noun into it.
    assert "mancata" in title
    assert "offline" in body
    # A missing last/next time renders as an em dash rather than crashing.
    assert "Esecuzione mancata: 2026-07-09 04:00" in body


def test_send_missed_backup_dispatches_when_on_failure_enabled():
    cfg = _notifications_config()
    cfg.notifications.on_failure = True
    fake = FakeApprise()
    svc = NotificationService(apprise_factory=lambda: fake)
    report = svc.send_missed_backup(
        cfg,
        _route(),
        datetime(2026, 7, 9, 4, 0, tzinfo=UTC),
        None,
        datetime(2026, 7, 12, 4, 0, tzinfo=UTC),
    )
    assert report.sent is True
    assert report.channels == 5
    assert fake.payload is not None and "missed scheduled run" in fake.payload[0]


def test_send_missed_backup_skipped_when_on_failure_disabled():
    cfg = _notifications_config()
    cfg.notifications.on_failure = False
    svc = NotificationService(apprise_factory=FakeApprise)
    report = svc.send_missed_backup(
        cfg, _route(), datetime(2026, 7, 9, 4, 0, tzinfo=UTC), None, None
    )
    assert report.sent is False
    assert report.skipped is True
    assert report.reason == "on_failure disabled"


def test_missed_backup_message_renders_every_time_in_the_configured_zone():
    """The missed/next times come from the cron trigger (already local) but the last run is
    read back from the database in UTC — so this message used to mix two zones, with one line
    silently hours off from the two around it."""
    cfg = Config()
    cfg.app.timezone = "Europe/Rome"  # UTC+2 in July
    _title, body = build_missed_backup_message(
        cfg,
        _route(),
        datetime(2026, 7, 9, 2, 0, tzinfo=UTC),
        datetime(2026, 7, 8, 2, 0, tzinfo=UTC),
        datetime(2026, 7, 10, 2, 0, tzinfo=UTC),
    )
    assert "Missed run: 2026-07-09 04:00 CEST" in body
    assert "Last run: 2026-07-08 04:00 CEST" in body
    assert "Next scheduled run: 2026-07-10 04:00 CEST" in body


def test_run_message_next_run_uses_the_configured_zone():
    cfg = Config()
    cfg.app.timezone = "Europe/Rome"
    _title, body = _msg(
        cfg, _run(RunStatus.SUCCESS), next_at=datetime(2026, 7, 10, 2, 0, tzinfo=UTC)
    )
    assert "Next scheduled run: 2026-07-10 04:00 CEST" in body


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


def test_interrupted_message_ignores_a_box_joulenap_never_powers():
    # A crash cannot leave an always-on PBS "burning power" — it was on before and after.
    cfg = Config.model_validate(
        {
            "pbss": [{"id": "pbs-01", "host": "192.0.2.20", "managed_power": False}],
            "routes": [{"id": "nightly", "kind": "verify", "target": "pbs-01"}],
        }
    )
    run = _run(RunStatus.FAILURE, error="Interrupted")
    run.route_id = "nightly"
    run.steps = [
        RunStep(name=StepName.WAIT, status=StepStatus.SUCCESS),
        RunStep(name=StepName.BACKUP, status=StepStatus.FAILURE),
    ]
    _title, body = build_interrupted_message(cfg, run)
    assert "left powered on" not in body


def test_interrupted_message_pairs_wake_and_power_off_per_device():
    """A sync route holds two boxes. One powering off must not hide the other staying up —
    the old rule ORed both steps across the whole run and went silent."""
    cfg = Config.model_validate(
        {
            "pbss": [
                {"id": "pbs-01", "host": "192.0.2.20", "mac": "00:11:22:33:44:55"},
                {"id": "pbs-02", "host": "192.0.2.21", "mac": "00:11:22:33:44:66"},
            ],
            "routes": [
                {"id": "off", "kind": "sync", "source_pbs": "pbs-01", "target": "pbs-02"}
            ],
        }
    )
    run = _run(RunStatus.FAILURE, error="Interrupted")
    run.route_id = "off"
    run.steps = [
        RunStep(name="wait:pbs-01", status=StepStatus.SUCCESS),
        RunStep(name="wait:pbs-02", status=StepStatus.SUCCESS),
        RunStep(name="poweroff:pbs-02", status=StepStatus.SUCCESS),
    ]
    _title, body = build_interrupted_message(cfg, run)
    assert "left powered on" in body  # pbs-01 is still up


def test_interrupted_message_no_pbs_line_when_it_never_woke():
    # Crashed during WAIT (PBS never came up): no "left on" warning.
    run = _run(RunStatus.FAILURE, error="Interrupted")
    run.steps = [
        RunStep(name=StepName.WAKE, status=StepStatus.SUCCESS),
        RunStep(name=StepName.WAIT, status=StepStatus.FAILURE),
    ]
    _title, body = build_interrupted_message(Config(), run)
    assert "left powered on" not in body


def test_interrupted_message_reports_how_long_the_pbs_has_been_awake():
    """This alert has no Duration line, and the interval that matters isn't the run's — it's
    how long the box has been burning power since it woke, which nothing has switched off."""
    run = _run(RunStatus.FAILURE, error="Interrupted")
    run.finished_at = datetime(2026, 6, 28, 13, 12, 0, tzinfo=UTC)  # the restart (sweep) time
    run.steps = [
        _step(StepName.WAIT, StepStatus.SUCCESS, 40),  # came up at 04:00:40
        RunStep(name=StepName.BACKUP, status=StepStatus.FAILURE),
    ]
    _title, body = build_interrupted_message(Config(), run)
    assert "PBS awake for: 9h 11m 20s" in body
    assert body.endswith("Run #128")


def test_interrupted_message_no_awake_line_when_it_never_woke():
    # WAIT failed: the box never came up, so there is no interval to report (and no warning).
    run = _run(RunStatus.FAILURE, error="Interrupted")
    run.steps = [_step(StepName.WAIT, StepStatus.FAILURE, 300)]
    _title, body = build_interrupted_message(Config(), run)
    assert "awake for" not in body


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


# --- the message packs -------------------------------------------------------


def _flatten(pack, prefix=""):
    out = {}
    for key, value in pack.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, name))
        else:
            out[name] = value
    return out


def test_every_language_pack_holds_the_same_keys():
    """``_pack`` falls back whole-pack, not per-key, so a key present in only one language
    raises ``KeyError`` inside a live notification send. Nothing else catches that."""
    for name, packs in (
        ("_MESSAGES", _MESSAGES),
        ("_KIND_LABEL", _KIND_LABEL),
        ("_ERRORS", _ERRORS),
        ("_DETAILS", _DETAILS),
    ):
        english = sorted(_flatten(packs["en"]))
        for language, pack in packs.items():
            assert sorted(_flatten(pack)) == english, f"{name}: '{language}' differs from 'en'"


def test_every_release_outcome_has_a_detail_string():
    """``ReleaseOutcome.key`` is derived from the member name, so a new member silently
    renders as the raw English until the packs learn about it. This is what notices."""
    for outcome in ReleaseOutcome:
        assert outcome.key in _DETAILS["en"], outcome
        # The enum's value doubles as the stored English detail: the two must agree, or the
        # timeline reads one thing in English and another in Italian.
        assert _DETAILS["en"][outcome.key] == outcome.value


def test_the_interrupted_step_detail_matches_the_catalogue():
    """``db.startup`` writes the English text and the key by hand (importing the recorder
    there would be a cycle), so nothing but this keeps the two in step."""
    assert _INTERRUPTED_STEP == _DETAILS["en"]["interrupted"]


def test_ad_hoc_maintenance_names_the_pbs_it_ran_on():
    """An ad-hoc GC/verify has no route, so nothing else in the body says *which* backup
    server it touched — with several configured, the title alone is ambiguous."""
    cfg = Config()
    run = _run(RunStatus.SUCCESS, kind=RunKind.GC)
    _, body = build_run_message(RunContext(config=cfg, run=run, pbs_id="pbs-02"))
    assert "PBS: pbs-02" in body
    # A route run keeps naming the route instead: its name already identifies the box.
    _, body = build_run_message(RunContext(config=cfg, run=run, route=_route(), pbs_id="pbs-02"))
    assert "PBS: pbs-02" not in body
    assert "Route: Nightly" in body


def test_error_keys_are_rendered_in_the_configured_language():
    cfg = Config()
    cfg.app.language = "it"
    run = _run(
        RunStatus.FAILURE,
        error="route 'nightly': pbs 'pbs-01' no longer exists",
        error_key="pbs_missing",
        error_params='{"route": "nightly", "pbs": "pbs-01"}',
    )
    _, body = build_run_message(RunContext(config=cfg, run=run))
    assert "Errore: route 'nightly': il pbs 'pbs-01' non esiste più" in body


def test_error_rendering_falls_back_to_the_stored_english_text():
    """The floor under every branch: a pre-1.0 row with no key, a key this version does not
    know, and a payload whose parameters no longer match the template."""
    cfg = Config()
    for key, params in (
        (None, None),
        ("no_such_key_in_this_version", '{"a": 1}'),
        ("pbs_missing", '{"wrong": "params"}'),
        ("pbs_missing", "not valid json at all"),
    ):
        run = _run(RunStatus.FAILURE, error="vzdump exploded", error_key=key, error_params=params)
        _, body = build_run_message(RunContext(config=cfg, run=run))
        assert "Error: vzdump exploded" in body, f"key={key!r} params={params!r}"


def test_missed_run_message_is_not_worded_for_backups():
    """catchup fires for every route kind, so a missed sync must not say "missed backup"."""
    cfg = Config()
    when = datetime(2026, 6, 28, 4, 0, 0, tzinfo=UTC)
    title, body = build_missed_backup_message(
        cfg, _route(kind="sync", target="pbs-02", source_pbs="pbs-01"), when, None, None
    )
    assert "backup" not in title.lower()
    assert "backup" not in body.lower()
    assert "(sync)" in body


def test_monitor_detail_stays_parseable_by_the_notifier():
    """``build_run_message`` reads the observed count out of the MONITOR step's *detail*
    (``int(detail.split()[0])``), which ``route_cycle._external_body`` writes as prose. This
    pins that contract: reword either side and this fails instead of the notification
    silently flipping to the "no PBS job ran" warning."""
    cfg = Config()
    run = _run(RunStatus.SUCCESS, kind=RunKind.MONITOR)
    run.steps = [
        RunStep(
            name=StepName.MONITOR,
            status=StepStatus.SUCCESS,
            started_at=run.started_at,
            detail=monitor_detail(3),
        )
    ]
    _, body = build_run_message(RunContext(config=cfg, run=run))
    assert "PBS jobs observed: 3" in body

    run.steps[0].detail = monitor_detail(None)
    _, body = build_run_message(RunContext(config=cfg, run=run))
    assert "No PBS job ran" in body


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


def test_body_is_declared_as_plain_text_so_html_channels_keep_the_line_breaks():
    """Every body is a "Label: value" list separated by newlines. Apprise only converts those
    newlines for an HTML-format channel (email, Telegram) when told the input is TEXT —
    without the declaration the whole body renders on a single line."""
    from apprise.common import NotifyFormat
    from apprise.conversion import convert_between

    fake = FakeApprise()
    svc = NotificationService(apprise_factory=lambda: fake)
    svc.send_test(_notifications_config())

    assert fake.body_format == NotifyFormat.TEXT
    rendered = convert_between(fake.body_format, NotifyFormat.HTML, "Duration: 8m\nGuests: 13")
    assert "<br/>" in rendered


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

        def notify(self, title: str = "", body: str = "", body_format: str = "") -> bool:
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

        def notify(self, title: str = "", body: str = "", body_format: str = "") -> bool:
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
        _send(svc, _notifications_config(), _run(RunStatus.SUCCESS))
    assert any("ntfy" in r.message and "boom" in r.message for r in caplog.records)


def test_success_skipped_when_on_success_disabled():
    cfg = _notifications_config()
    cfg.notifications.on_success = False
    svc = NotificationService(apprise_factory=FakeApprise)
    report = _send(svc, cfg, _run(RunStatus.SUCCESS))
    assert report.skipped is True


def test_failure_sent_when_on_failure_enabled():
    fake = FakeApprise()
    cfg = _notifications_config()
    svc = NotificationService(apprise_factory=lambda: fake)
    report = _send(svc, cfg, _run(RunStatus.FAILURE, error="boom"))
    assert report.sent is True
    assert "boom" in fake.payload[1]


# --- who sends it, and when --------------------------------------------------
#
# The cycle no longer notifies: it returns a RunContext and JobService._execute sends it
# *after* releasing the power leases, which is the only moment "did the box go back to
# sleep" is knowable. These cover that hand-off.


def _queued_service(temp_store, notify):
    from fakes import FakeBox, FakePbs

    from app.jobs.service import JobService

    deps, *_ = make_deps(pbss={"pbs-01": FakePbs(), "pbs-02": FakePbs()}, notify=notify)
    return JobService(temp_store, deps=deps, lease_deps=FakeBox().deps())


def _drain(service) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


def test_the_notification_is_sent_after_the_run_finished(temp_config, temp_db):
    from app.core.config_store import ConfigStore

    seen: list[RunContext] = []
    service = _queued_service(ConfigStore.load_or_create(), seen.append)

    service.run_maintenance("pbs-01", "gc")
    _drain(service)

    assert len(seen) == 1
    ctx = seen[0]
    assert ctx.run.status == RunStatus.SUCCESS
    assert ctx.datastore is not None  # read while the PBS was still awake
    assert ctx.route is None  # an ad-hoc maintenance run belongs to no route


def test_a_route_run_carries_its_route_and_next_fire(temp_config, temp_db):
    from app.core.config_store import ConfigStore

    seen: list[RunContext] = []
    service = _queued_service(ConfigStore.load_or_create(), seen.append)
    when = datetime(2026, 7, 12, 2, 0, tzinfo=UTC)
    service.deps.next_run = lambda route_id: when if route_id == "offsite" else None

    service.run_route("offsite")
    _drain(service)

    assert len(seen) == 1
    assert seen[0].route.id == "offsite"
    assert seen[0].next_at == when


def test_a_muted_route_is_not_notified():
    cfg = _notifications_config()
    svc = NotificationService(apprise_factory=FakeApprise)
    report = _send(svc, cfg, _run(RunStatus.SUCCESS), route=_route(notify=False))
    assert report.skipped is True
    assert "notify off" in report.reason


def test_a_muted_route_is_not_told_it_missed_a_run():
    # A route the user muted stays muted even when the news is that it did not run.
    cfg = _notifications_config()
    svc = NotificationService(apprise_factory=FakeApprise)
    report = svc.send_missed_backup(
        cfg, _route(notify=False), datetime(2026, 7, 9, 4, 0, tzinfo=UTC), None, None
    )
    assert report.skipped is True


def test_a_notify_failure_does_not_break_the_run(temp_config, temp_db):
    from app.core.config_store import ConfigStore

    def boom(_ctx):
        raise RuntimeError("smtp down")

    service = _queued_service(ConfigStore.load_or_create(), boom)

    service.run_maintenance("pbs-01", "gc")
    _drain(service)

    with session_scope() as session:
        run = session.scalars(select(Run)).one()
        assert run.status == RunStatus.SUCCESS
        messages = [e.message for e in run.logs]
    assert any("notification failed" in m for m in messages)


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
