"""Server-side, localized notification text.

Notification bodies are built and translated **on the backend** before sending — they
never pass through the frontend i18n. This is a small dictionary keyed by the
``app.language`` config, with an English fallback, mirroring the per-language approach of
the UI locales but kept deliberately tiny (only the strings that ship in a notification).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import TYPE_CHECKING

from ..config import Config, Route
from ..db.models import Run, RunStatus, RunStep, StepName, StepStatus

if TYPE_CHECKING:
    from ..connectors.pbs import DatastoreStatus


@dataclass
class GuestSummary:
    """What the vzdump task did per guest, for the notification's ``Guests`` line.

    Deliberately **not** persisted: ``runs`` has no migration path (``init_db`` only calls
    ``create_all``, which never adds a column to an existing table), so this rides along to
    the notifier as an argument exactly like :class:`DatastoreStatus` does.

    ``failed`` holds display names (the vmid when the guest's name is unknown). Only guests
    vzdump actually reported on can land here, so a guest the user excluded from the
    selection is never counted or named.
    """

    total: int = 0  # guests the run set out to back up
    ok: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class RunContext:
    """Everything a finished run's notification can say about itself.

    One object instead of the positional ``(config, run, datastore, guests, next_at)``
    tuple that used to be threaded through ``CycleDeps.notify`` ->
    ``NotificationService.send_run_result`` -> ``build_run_message``: adding a field there
    meant editing every call site and every fake, and a caller passing its arguments in
    the wrong order got a silently wrong message instead of a TypeError. Everything past
    the first two is optional, so a cycle fills in only what it actually knows.
    """

    config: Config
    run: Run
    #: The route this run belongs to, if any — an ad-hoc PBS GC/verify has none. Carries
    #: the per-route ``notify`` filter and names the route in the body.
    route: Route | None = None
    datastore: DatastoreStatus | None = None
    guests: GuestSummary | None = None
    #: When this route next fires, for the "Next scheduled run" line.
    next_at: datetime | None = None
    #: PBS ids this run left awake and burning power, with nothing queued to shut them
    #: down. Filled in by ``JobService`` after the leases are released: it is the only
    #: place that knows *why* a box stayed on, and only some of the reasons cost energy
    #: (an always-on box, or one another run still holds, cost nothing).
    left_on: list[str] = field(default_factory=list)


# event keys: success | failure | aborted | test
_MESSAGES: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "success": {"title": "✅ Joulenap — backup succeeded"},
        "failure": {"title": "❌ Joulenap — backup failed"},
        "aborted": {"title": "⚠️ Joulenap — backup aborted"},
        # Per-kind titles for the non-backup cycles (see _title_for). Full strings rather
        # than a "{noun} succeeded" template: Italian needs gender agreement (backup
        # riuscito / verifica riuscita), which a noun slot can't express.
        "gc": {
            "success": "✅ Joulenap — garbage collection succeeded",
            "failure": "❌ Joulenap — garbage collection failed",
            "aborted": "⚠️ Joulenap — garbage collection aborted",
        },
        "verify": {
            "success": "✅ Joulenap — verification succeeded",
            "failure": "❌ Joulenap — verification failed",
            "aborted": "⚠️ Joulenap — verification aborted",
        },
        # External-schedules mode: the watch cycle (wake -> watch PVE/PBS's own jobs ->
        # power off).
        "monitor": {
            "success": "✅ Joulenap — PBS jobs finished",
            "failure": "❌ Joulenap — watch cycle failed",
            "aborted": "⚠️ Joulenap — watch cycle aborted",
        },
        "sync": {
            "success": "✅ Joulenap — sync succeeded",
            "failure": "❌ Joulenap — sync failed",
            "aborted": "⚠️ Joulenap — sync aborted",
        },
        # Route-neutral: catchup fires for every kind, so a missed sync or verify must not
        # be announced as a missed backup. The kind is named on the Route line below.
        "missed": {
            "title": "⚠️ Joulenap — missed scheduled run",
            "intro": "A scheduled route did not run because Joulenap was offline when it "
            "was due.",
        },
        "interrupted": {
            "title": "⚠️ Joulenap — run interrupted by a restart",
            "intro": "Joulenap restarted while a run was in progress; it was marked failed.",
        },
        "test": {
            "title": "🔔 Joulenap — test notification",
            "body": "If you can read this, notifications are configured correctly.",
        },
        "_labels": {
            "route": "Route",
            "trigger": "Trigger",
            "trigger_scheduled": "scheduled",
            "trigger_manual": "manual",
            "duration": "Duration",
            "guests": "Guests",
            "failed": "failed",
            "datastore": "Datastore",
            "used": "used",
            "free": "free",
            "error": "Error",
            "pbs_left_on": "⚠️ PBS left powered on — check it",
            "awake_for": "PBS awake for",
            "missed_run": "Missed run",
            "last_run": "Last run",
            "next_run": "Next scheduled run",
            "run_no": "Run",
            # Names for the duration breakdown. Only the phases that do the actual work: the
            # wake packet is instant, and wait/power-off are near-constant overhead that would
            # just crowd the line.
            "phase_backup": "backup",
            "phase_gc": "GC",
            "phase_verify": "verify",
            "phase_monitor": "watch",
            "tasks_observed": "PBS jobs observed",
            "no_tasks": "⚠️ No PBS job ran — check the schedules on PVE/PBS",
        },
    },
    "it": {
        "success": {"title": "✅ Joulenap — backup riuscito"},
        "failure": {"title": "❌ Joulenap — backup fallito"},
        "aborted": {"title": "⚠️ Joulenap — backup interrotto"},
        "gc": {
            "success": "✅ Joulenap — garbage collection riuscita",
            "failure": "❌ Joulenap — garbage collection fallita",
            "aborted": "⚠️ Joulenap — garbage collection interrotta",
        },
        "verify": {
            "success": "✅ Joulenap — verifica riuscita",
            "failure": "❌ Joulenap — verifica fallita",
            "aborted": "⚠️ Joulenap — verifica interrotta",
        },
        "monitor": {
            "success": "✅ Joulenap — job PBS completati",
            "failure": "❌ Joulenap — ciclo di controllo fallito",
            "aborted": "⚠️ Joulenap — ciclo di controllo interrotto",
        },
        "sync": {
            "success": "✅ Joulenap — sincronizzazione riuscita",
            "failure": "❌ Joulenap — sincronizzazione fallita",
            "aborted": "⚠️ Joulenap — sincronizzazione interrotta",
        },
        "missed": {
            "title": "⚠️ Joulenap — esecuzione pianificata mancata",
            "intro": "Una route pianificata non è stata eseguita perché Joulenap era "
            "offline al momento previsto.",
        },
        "interrupted": {
            "title": "⚠️ Joulenap — esecuzione interrotta da un riavvio",
            "intro": "Joulenap si è riavviato mentre un'esecuzione era in corso; "
            "è stata contrassegnata come fallita.",
        },
        "test": {
            "title": "🔔 Joulenap — notifica di prova",
            "body": "Se leggi questo messaggio, le notifiche sono configurate correttamente.",
        },
        "_labels": {
            "route": "Route",
            "trigger": "Avvio",
            "trigger_scheduled": "pianificato",
            "trigger_manual": "manuale",
            "duration": "Durata",
            "guests": "Guest",
            "failed": "falliti",
            "datastore": "Datastore",
            "used": "usato",
            "free": "liberi",
            "error": "Errore",
            "pbs_left_on": "⚠️ PBS lasciato acceso — controllalo",
            "awake_for": "PBS sveglio da",
            "missed_run": "Esecuzione mancata",
            "last_run": "Ultima esecuzione",
            "next_run": "Prossima esecuzione pianificata",
            "run_no": "Run",
            "phase_backup": "backup",
            "phase_gc": "GC",
            "phase_verify": "verifica",
            "phase_monitor": "controllo",
            "tasks_observed": "Job PBS osservati",
            "no_tasks": "⚠️ Nessun job PBS eseguito — controlla le pianificazioni su PVE/PBS",
        },
    },
}

#: Why a run failed, as ``str.format`` templates. A run's ``error`` is shown in **two**
#: places — the notification body and the run-history row — so it is stored as a key plus
#: parameters (``runs.error_key`` / ``runs.error_params``) and rendered at read time, in the
#: configured language, by :func:`render_error`. ``runs.error`` still holds the English
#: rendering: it is what a pre-1.0 row has, and the fallback for anything not in here.
#:
#: Deliberately **not** covered: text that came from someone else's software (a PBS task
#: log, an httpx or paramiko exception). That rides along inside ``unexpected``/``sync``
#: verbatim — a dictionary of other people's error messages is not a thing to maintain.
_ERRORS: dict[str, dict[str, str]] = {
    "en": {
        "unexpected": "unexpected error: {detail}",
        "unknown": "unexpected error",
        "cancelled": "Cancelled by user",
        "cancelled_waiting": "Cancelled while waiting for PBS '{pbs}'",
        "interrupted": "Interrupted — Joulenap restarted while the run was in progress",
        "pbs_missing": "route '{route}': pbs '{pbs}' no longer exists",
        "pbs_unreachable": (
            "PBS '{pbs}' ({host}:{port}) not reachable after {attempts} wake "
            "attempt(s) of {timeout}s each"
        ),
        "source_pve_missing": "source pve '{pve}' no longer exists",
        "no_storage_mapping": (
            "pve '{pve}' has no storage mapping for pbs '{pbs}' (Datacenter > Storage)"
        ),
        "no_guests": "pve '{pve}': no guests selected for backup",
        "datastore_full": (
            "PBS '{pbs}' datastore '{datastore}' only {free}% free "
            "(need >= {threshold}%); skipping backup"
        ),
        "target_pbs_missing": "route '{route}': target pbs '{pbs}' no longer exists",
        "sources_failed": "backup failed for source(s): {sources}",
        "unsupported_kind": "route '{route}': unsupported kind '{kind}'",
        "unsupported_action": "unsupported maintenance action '{action}'",
        "sync_failed": "sync {direction} {source} -> {target} failed ({status}){reason}",
    },
    "it": {
        "unexpected": "errore imprevisto: {detail}",
        "unknown": "errore imprevisto",
        "cancelled": "Annullato dall'utente",
        "cancelled_waiting": "Annullato durante l'attesa del PBS '{pbs}'",
        "interrupted": "Interrotta — Joulenap si è riavviato mentre l'esecuzione era in corso",
        "pbs_missing": "route '{route}': il pbs '{pbs}' non esiste più",
        "pbs_unreachable": (
            "PBS '{pbs}' ({host}:{port}) non raggiungibile dopo {attempts} tentativi di "
            "risveglio da {timeout}s ciascuno"
        ),
        "source_pve_missing": "il pve sorgente '{pve}' non esiste più",
        "no_storage_mapping": (
            "il pve '{pve}' non ha una mappatura storage verso il pbs '{pbs}' "
            "(Datacenter > Storage)"
        ),
        "no_guests": "pve '{pve}': nessun guest selezionato per il backup",
        "datastore_full": (
            "il datastore '{datastore}' del PBS '{pbs}' ha solo il {free}% libero "
            "(serve almeno {threshold}%); backup saltato"
        ),
        "target_pbs_missing": "route '{route}': il pbs di destinazione '{pbs}' non esiste più",
        "sources_failed": "backup fallito per la/e sorgente/i: {sources}",
        "unsupported_kind": "route '{route}': tipo '{kind}' non supportato",
        "unsupported_action": "azione di manutenzione '{action}' non supportata",
        "sync_failed": (
            "sincronizzazione {direction} {source} -> {target} fallita ({status}){reason}"
        ),
    },
}

#: Step ``detail`` strings Joulenap authored, i.e. the ones it can translate. The English
#: rendering is still stored on the row (``run_steps.detail``) and is the fallback here, the
#: same contract ``_ERRORS`` has with ``runs.error``.
#:
#: Deliberately **not** covered, and deliberately keyless: a PVE/PBS task UPID and the text
#: of someone else's exception. Those are identifiers and foreign strings, not copy.
_DETAILS: dict[str, dict[str, str]] = {
    "en": {
        "already_awake": "already awake",
        "woken": "woken by Wake-on-LAN",
        "powered_off": "powered off",
        "still_needed": "left on: still needed by another run",
        "unmanaged": "left on: Joulenap does not manage this box's power",
        "left_on": "left powered on",
        "gc_disabled": "GC disabled for this route",
        "verify_disabled": "verify disabled for this route",
        "free_space": "{free}% free ({avail} GB)",
        "tasks_observed": "{count} task(s) observed",
        "no_tasks_observed": "no tasks observed",
        "interrupted": "Interrupted at startup",
    },
    "it": {
        "already_awake": "già acceso",
        "woken": "acceso con Wake-on-LAN",
        "powered_off": "spento",
        "still_needed": "lasciato acceso: serve ancora a un'altra esecuzione",
        "unmanaged": "lasciato acceso: Joulenap non gestisce l'alimentazione di questa macchina",
        "left_on": "lasciato acceso",
        "gc_disabled": "GC disattivata per questa route",
        "verify_disabled": "verifica disattivata per questa route",
        "free_space": "{free}% libero ({avail} GB)",
        "tasks_observed": "{count} task osservati",
        "no_tasks_observed": "nessun task osservato",
        "interrupted": "Interrotto al riavvio",
    },
}


def _run_error(language: str, run: Run) -> str | None:
    """A run's failure message in ``language``, from the stored key when it has one.

    The API renders the same thing for the history row (``api.schemas.RunSummary.of``); this
    is the notification's half. Both fall back to ``run.error``, the English rendering the
    recorder always stores.
    """
    params: Mapping[str, object] | None = None
    if run.error_params:
        try:
            decoded = json.loads(run.error_params)
        except ValueError:
            decoded = None
        params = decoded if isinstance(decoded, dict) else None
    return render_error(language, run.error_key, params, run.error)


class LocalizedError(Exception):
    """An error that remembers *what* went wrong, not just how it reads in English.

    ``str(exc)`` still yields the English sentence, so every existing ``error=str(exc)``
    path and every log line keeps working unchanged; ``key``/``params`` ride alongside so
    the recorder can persist them and the message can be rebuilt in the user's language
    when it is read back.
    """

    def __init__(self, key: str, /, **params: object) -> None:
        self.key = key
        self.params = params
        super().__init__(render_error("en", key, params) or key)


_STATUS_EVENT = {
    RunStatus.SUCCESS: "success",
    RunStatus.FAILURE: "failure",
    RunStatus.ABORTED: "aborted",
}


def _pack(language: str) -> dict[str, dict[str, str]]:
    return _MESSAGES.get(language, _MESSAGES["en"])


def _render(
    catalogue: dict[str, dict[str, str]],
    language: str,
    key: str | None,
    params: Mapping[str, object] | None,
    raw: str | None,
) -> str | None:
    """Look ``key`` up in ``catalogue`` and fill it in, degrading to ``raw`` at every step.

    Every branch degrades to readable text rather than raising: a pre-1.0 row has no key, a
    key added in a later version may be missing from a pack, and a template whose parameters
    changed would otherwise ``KeyError`` inside a notification send. The stored English
    ``raw`` is always a usable answer, so it is the floor.
    """
    if not key:
        return raw
    template = catalogue.get(language, catalogue["en"]).get(key) or catalogue["en"].get(key)
    if template is None:
        return raw
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError, ValueError):
        return raw or template


def render_error(
    language: str,
    key: str | None,
    params: Mapping[str, object] | None,
    raw: str | None = None,
) -> str | None:
    """The failure message in ``language``, falling back to ``raw`` whenever it cannot be built."""
    return _render(_ERRORS, language, key, params, raw)


def render_detail(
    language: str,
    key: str | None,
    params: Mapping[str, object] | None,
    raw: str | None = None,
) -> str | None:
    """A step's ``detail`` in ``language``, falling back to the stored English ``raw``.

    The same seam as :func:`render_error`, one level down. Only details Joulenap authored
    carry a key; a task UPID or a connector's own error text has none and comes back
    verbatim, which is what ``raw`` is for.
    """
    return _render(_DETAILS, language, key, params, raw)


def _title_for(pack: dict[str, dict[str, str]], kind: str, event: str) -> str:
    """Title for a finished run, worded for the kind of cycle it was.

    A GC or verify cycle reports its own outcome instead of borrowing the backup wording (a
    scheduled verify failure used to notify "backup failed"). Anything without its own block
    — a normal backup cycle, or a kind added later — falls back to the backup title, so a new
    ``RunKind`` degrades to today's behaviour instead of raising.
    """
    return pack.get(kind, {}).get(event) or pack[event]["title"]


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


#: Steps that get a slot in the duration breakdown, keyed to their ``_labels`` entry. Wake,
#: wait, precheck and power-off are left out on purpose — see the label block's comment.
_PHASE_LABEL = {
    StepName.BACKUP: "phase_backup",
    StepName.MONITOR: "phase_monitor",
    StepName.GC: "phase_gc",
    StepName.VERIFY: "phase_verify",
}


def _phase_breakdown(labels: dict[str, str], run: Run) -> str:
    """``backup 7m 10s · GC 1m 6s`` — where the run's time actually went, in step order.

    Skipped steps (GC turned off) and steps still running contribute nothing, so the
    parentheses never advertise work that didn't happen. A ``StepName`` added later simply
    doesn't appear rather than raising.

    Matched with ``_step_is`` rather than by equality, and summed per phase: a backup route
    records one step **per source PVE** (``backup:pve-alpha``), so an equality test matched
    nothing at all and every backup notification silently lost the one slice that mattered.
    """
    totals: dict[str, float] = {}
    for step in run.steps:  # the relationship is ordered by started_at
        if step.status == StepStatus.SKIPPED or not step.finished_at:
            continue
        key = next((k for n, k in _PHASE_LABEL.items() if _step_is(step, n)), None)
        if key is None:
            continue
        seconds = (step.finished_at - step.started_at).total_seconds()
        totals[key] = totals.get(key, 0.0) + seconds
    return " · ".join(f"{labels[k]} {_format_duration(s)}" for k, s in totals.items())


def human_bytes(n: int) -> str:
    """Binary-unit size, e.g. ``4.6 TiB`` (PBS reports datastore sizes in bytes)."""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"


def _step_is(step: RunStep, name: StepName) -> bool:
    """Whether ``step`` is an instance of ``name``, labelled or not.

    A run touching several devices records ``wait:pbs-01`` / ``poweroff:pbs-02`` (see
    ``RunRecorder.step``'s ``label``), so an equality test would silently stop matching the
    moment a route had more than one target.
    """
    return step.name.split(":", 1)[0] == name.value


def _step_label(step: RunStep) -> str | None:
    """The device a step names (``poweroff:pbs-02`` -> ``pbs-02``), or None when unlabelled
    — a single-device run doesn't repeat which box it means."""
    _, _, label = step.name.partition(":")
    return label or None


def _pbs_left_on(config: Config, run: Run) -> bool:
    """True if a PBS came up and nothing ever powered it back off — a box still burning
    energy that the user should go and check.

    Only for a run a restart interrupted: there the run row is all there is, and no
    POWEROFF step was ever reached. A run that *finished* knows the answer exactly and
    reports it through ``RunContext.left_on``, because "was it left on?" depends on facts
    the timeline doesn't carry (whether another queued route still needs the box).

    Two things the steps alone get wrong, both introduced by this same release:

      * an **unmanaged** box (``managed_power: false``) is never Joulenap's to power down,
        so a run against one must not warn — hence taking ``config``;
      * a run holding **several** leases needs the WAIT and POWEROFF steps paired *per
        device*, or one box's successful power-off hides another's that stayed up.

    An abort *before* the box came up leaves the WAIT step non-SUCCESS, so the PBS is off
    and this correctly returns False — hence why it keys on WAIT, not on the run status.
    """
    managed = {p.id for p in config.pbss if p.managed_power}
    route = next((r for r in config.routes if r.id == run.route_id), None)
    # An unlabelled step belongs to the run's only box — a route's target (a sync route
    # labels both sides). An ad-hoc GC/verify records neither a label nor a route, so its
    # box cannot be named: warn rather than stay silent about a box that may be awake.
    default = route.target if route else None
    powered_off = {
        _step_label(s)
        for s in run.steps
        if _step_is(s, StepName.POWEROFF) and s.status == StepStatus.SUCCESS
    }
    for step in run.steps:
        if not _step_is(step, StepName.WAIT) or step.status != StepStatus.SUCCESS:
            continue
        pbs_id = _step_label(step) or default
        if pbs_id is not None and pbs_id not in managed:
            continue
        if _step_label(step) not in powered_off:
            return True
    return False


#: Route kinds, for the body's ``Route:`` line. Localized because the kind is a user-facing
#: word in the UI too; an unknown kind falls through to its raw value.
_KIND_LABEL = {
    "en": {"backup": "backup", "sync": "sync", "external": "external", "verify": "verify"},
    "it": {
        "backup": "backup",
        "sync": "sincronizzazione",
        "external": "esterna",
        "verify": "verifica",
    },
}


def _kind_label(config: Config, route: Route) -> str:
    """The route's kind as a word, in the configured language; unknown kinds pass through."""
    kinds = _KIND_LABEL.get(config.app.language, _KIND_LABEL["en"])
    return kinds.get(route.kind, route.kind)


def build_run_message(ctx: RunContext) -> tuple[str, str]:
    """``(title, body)`` describing a finished run, in the configured language.

    One field per line, in a fixed order; a field whose data is missing drops out entirely
    rather than rendering a placeholder. ``ctx.datastore`` (read while the PBS was still
    awake) adds the usage line, ``ctx.guests`` the per-guest tally of a backup cycle,
    ``ctx.next_at`` the schedule's following fire.
    """
    config, run = ctx.config, ctx.run
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    event = _STATUS_EVENT.get(run.status, "failure")  # RUNNING shouldn't reach here

    # The title already conveys success/failure/aborted, so we don't repeat the (untranslated)
    # status enum in the body.
    lines: list[str] = []
    if ctx.route is not None:
        # The route's *colour* is deliberately absent: it is a UI affordance, and a hex
        # string in a push notification is noise on every channel that could render it.
        name = ctx.route.name or ctx.route.id
        lines.append(f"{labels['route']}: {name} ({_kind_label(config, ctx.route)})")
    lines.append(f"{labels['trigger']}: {labels.get(f'trigger_{run.trigger}', run.trigger)}")
    datastore, guests, next_at = ctx.datastore, ctx.guests, ctx.next_at
    if run.started_at and run.finished_at:
        duration = (run.finished_at - run.started_at).total_seconds()
        breakdown = _phase_breakdown(labels, run)
        line = f"{labels['duration']}: {_format_duration(duration)}"
        lines.append(f"{line} ({breakdown})" if breakdown else line)
    # No summary at all (a GC or verify cycle, or an abort before the guests were picked)
    # means there is nothing truthful to say about guests — better silent than "0".
    if guests is not None and guests.total:
        line = f"{labels['guests']}: {guests.ok}/{guests.total}"
        if guests.failed:
            line += f" ({labels['failed']}: {', '.join(guests.failed)})"
        lines.append(line)
    # An External route's watch: the MONITOR step's detail is either "N task(s) observed"
    # or "no tasks observed" (see route_cycle._external_body) — the count line for the
    # former, the your-schedule-didn't-fire warning for the latter.
    monitor = next((s for s in run.steps if s.name == StepName.MONITOR), None)
    if monitor is not None and monitor.detail:
        try:
            observed = int(monitor.detail.split()[0])
        except ValueError:
            lines.append(labels["no_tasks"])
        else:
            lines.append(f"{labels['tasks_observed']}: {observed}")
    if datastore is not None:
        lines.append(
            f"{labels['datastore']}: {datastore.used_pct}% {labels['used']}, "
            f"{human_bytes(datastore.avail)} {labels['free']}"
        )
    if error := _run_error(config.app.language, run):
        lines.append(f"{labels['error']}: {error}")

    # From the service, not from the steps: a finished run knows exactly which boxes it
    # left burning power, and only those warrant the warning.
    if ctx.left_on:
        lines.append(labels["pbs_left_on"])

    if next_at is not None:
        lines.append(f"{labels['next_run']}: {_format_dt(next_at, _timezone(config))}")
    if run.id is not None:
        lines.append(f"{labels['run_no']} #{run.id}")

    return _title_for(pack, run.kind, event), "\n".join(lines)


def _timezone(config: Config) -> tzinfo:
    """The zone every timestamp in a notification is rendered in.

    Imported here rather than at module scope because ``core.scheduler`` pulls in ``jobs``,
    which pulls in this package — a top-level import would be circular."""
    from ..core.scheduler import resolve_timezone

    return resolve_timezone(config.app.timezone)


def _format_dt(dt: datetime | None, tz: tzinfo) -> str:
    """A short absolute timestamp for notifications, e.g. ``2026-07-11 04:00 CEST``.

    Everything is converted into the configured zone first. Cron-derived times already
    arrive in it, but anything read back from the database is UTC (see ``UtcDateTime``) —
    without the conversion one line of the same message would silently be hours off from
    the ones around it."""
    if dt is None:
        return "—"
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z").rstrip()


def build_missed_backup_message(
    config: Config,
    route: Route,
    missed_at: datetime,
    last_run_at: datetime | None,
    next_at: datetime | None,
) -> tuple[str, str]:
    """``(title, body)`` for a scheduled route that didn't run because the process was down
    over its window (BE-R1), in the configured language.

    Names the route: with per-route schedules, "a scheduled backup was skipped" no longer
    identifies which one.
    """
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    tz = _timezone(config)
    lines = [
        pack["missed"]["intro"],
        "",
        f"{labels['route']}: {route.name or route.id} ({_kind_label(config, route)})",
        f"{labels['missed_run']}: {_format_dt(missed_at, tz)}",
        f"{labels['last_run']}: {_format_dt(last_run_at, tz)}",
        f"{labels['next_run']}: {_format_dt(next_at, tz)}",
    ]
    return pack["missed"]["title"], "\n".join(lines)


def build_interrupted_message(config: Config, run: Run) -> tuple[str, str]:
    """``(title, body)`` for a run that a restart interrupted (swept to FAILURE at startup,
    BE-R2), in the configured language.

    Adds the "PBS left powered on" warning when a box Joulenap powers had actually woken
    before the crash (WAIT succeeded, no matching POWEROFF) — the whole point of the alert:
    a normally-off box that a crash left awake and burning power."""
    pack = _pack(config.app.language)
    labels = pack["_labels"]
    lines = [pack["interrupted"]["intro"]]
    if error := _run_error(config.app.language, run):
        lines.append(f"{labels['error']}: {error}")
    if _pbs_left_on(config, run):
        lines.append(labels["pbs_left_on"])
        # This alert has no Duration line (the run's own span would span the whole downtime,
        # not the work), so the one interval worth reporting is how long the box has been
        # awake: from the moment it came up to the restart — and, since nothing powered it
        # off, counting still.
        awake_since = _awake_since(run)
        if awake_since and run.finished_at:
            awake = (run.finished_at - awake_since).total_seconds()
            lines.append(f"{labels['awake_for']}: {_format_duration(awake)}")
    if run.id is not None:
        lines.append(f"{labels['run_no']} #{run.id}")
    return pack["interrupted"]["title"], "\n".join(lines)


def _awake_since(run: Run) -> datetime | None:
    """When the PBS finished coming up — the WAIT step's finish, or None if it never did."""
    for step in run.steps:
        if _step_is(step, StepName.WAIT) and step.status == StepStatus.SUCCESS:
            return step.finished_at
    return None


def build_test_message(config: Config) -> tuple[str, str]:
    """``(title, body)`` for the manual 'send test notification' action."""
    pack = _pack(config.app.language)
    return pack["test"]["title"], pack["test"]["body"]
