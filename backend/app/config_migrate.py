"""Automatic 0.9 -> 1.0 config migration.

0.9 modelled exactly one PVE, one PBS and one backup job (``pve:``/``pbs:``/``backup:``).
1.0 models any number of each plus explicit routes. An existing ``config.yaml`` is converted
in place on the first start after the upgrade, so users notice nothing.

Two rules keep this from ever bricking a boot (the BE-B1 lesson):

* the original file is copied to ``config.yaml.pre-overhaul.bak`` before anything is
  rewritten, and an existing ``.bak`` is never overwritten — the first migration wins;
* the converted config is validated before it is adopted. If anything about it fails to
  validate, or the file cannot be written, the original is kept and the app starts on it.

The 0.9 sections are dropped from the converted config — nothing reads them any more, and
``config.yaml.pre-overhaul.bak`` is the rollback path if one is ever needed.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, List  # noqa: UP035  (List mirrors config.py's 3.14 workaround)

log = logging.getLogger(__name__)

BACKUP_SUFFIX = ".pre-overhaul.bak"

# The 0.9 job becomes a route in the app's accent colour; a scheduled verify becomes a
# second one in the teal the UI uses for verify badges.
BACKUP_ROUTE_COLOR = "#e8830f"
VERIFY_ROUTE_COLOR = "#14b8a6"

_SLUG_STRIP = re.compile(r"[^a-z0-9._-]+")
_SLUG_LEAD = re.compile(r"^[^a-z0-9]+")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_INT = re.compile(r"^\d+$")

# Cron numbers weekdays 0=Sun..6=Sat (7 is Sunday too); our days[] is 0=Mon..6=Sun.
_CRON_DOW_TO_INDEX = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}


def needs_migration(raw: dict[str, Any]) -> bool:
    """True for a config written by 0.9: it has the old sections and no ``routes`` key.

    Key *presence* is what marks a config as migrated, not a non-empty route list — a user
    who deletes their last route must not have one conjured back on the next boot.
    """
    return "routes" not in raw and any(k in raw for k in ("pve", "pbs", "backup"))


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a 1.0 copy of ``raw``: ``pves``/``pbss``/``routes`` built from the 0.9
    sections, which are then dropped. Pure — no file access.

    ``.pre-overhaul.bak`` is the rollback path, not the old keys: nothing reads them after
    this, and leaving them in would fail validation (``_Base`` forbids extra keys).
    """
    out = dict(raw)
    pve = _section(raw, "pve")
    pbs = _section(raw, "pbs")
    backup = _section(raw, "backup")
    maintenance = _section(raw, "maintenance")

    pve_id = _device_id(pve.get("node"), pve.get("host"), "pve")
    pbs_id = _device_id(None, pbs.get("host"), "pbs")
    if pve_id == pbs_id:  # a host named the same on both sides
        pbs_id = f"{pbs_id}-pbs"

    pves = [_pve_device(pve, pve_id, pbs_id)] if pve.get("host") else []
    pbss = [_pbs_device(pbs, pbs_id, backup)] if pbs.get("host") else []
    routes = _routes(backup, maintenance, pves, pbss, pve_id, pbs_id)

    out["pves"] = pves
    out["pbss"] = pbss
    out["routes"] = routes
    for key in ("pve", "pbs", "backup"):
        out.pop(key, None)
    if isinstance(out.get("maintenance"), dict):
        out["maintenance"] = {
            k: v for k, v in out["maintenance"].items() if k not in ("gc", "verify")
        }
    return out


def write_backup(path: Path) -> None:
    """Copy the pre-migration config beside itself as the rollback parachute.

    An existing backup is never overwritten: the first migration wins, so re-running this
    can't lose the true 0.9 original. A backup that can't be written (read-only mount) is
    logged, not fatal — the migration only *adds* sections, so the old ones stay as the
    in-file fallback either way.
    """
    bak = path.with_name(path.name + BACKUP_SUFFIX)
    if bak.exists():
        return
    try:
        shutil.copyfile(path, bak)
        log.info("config: saved a pre-migration copy at %s", bak)
    except OSError as exc:
        log.warning("config: could not write %s (%s) — migrating anyway", bak, exc)


# --- devices ------------------------------------------------------------------


def _pve_device(pve: dict[str, Any], pve_id: str, pbs_id: str) -> dict[str, Any]:
    device = {
        "id": pve_id,
        "host": pve.get("host", ""),
        "port": pve.get("port", 8006),
        "verify_tls": pve.get("verify_tls", False),
        "api_token_id": pve.get("api_token_id", ""),
        "api_token_secret": pve.get("api_token_secret", ""),
        "storages": {},
    }
    # 0.9's single storage_id is how THIS pve names THAT pbs.
    if pve.get("storage_id"):
        device["storages"] = {pbs_id: pve["storage_id"]}
    return device


def _pbs_device(pbs: dict[str, Any], pbs_id: str, backup: dict[str, Any]) -> dict[str, Any]:
    mac = pbs.get("mac", "")
    if not mac:
        # 0.9 could not have woken this box either, so record what is actually true rather
        # than writing a config that fails the managed_power contract.
        log.warning("config: pbs '%s' has no MAC — migrating it as managed_power: false", pbs_id)
    external = _section(backup, "external")
    return {
        "id": pbs_id,
        "host": pbs.get("host", ""),
        "port": pbs.get("port", 8007),
        "datastore": pbs.get("datastore", ""),
        "fingerprint": pbs.get("fingerprint", ""),
        "api_token_id": pbs.get("api_token_id", ""),
        "api_token_secret": pbs.get("api_token_secret", ""),
        "managed_power": bool(mac),
        "mac": mac,
        "wol_broadcast_iface": pbs.get("wol_broadcast_iface", ""),
        "wait_timeout": pbs.get("wait_timeout", 180),
        "wol_retries": pbs.get("wol_retries", 2),
        "poweroff_task_wait": pbs.get("poweroff_task_wait", 600),
        "ssh_user": pbs.get("ssh_user", "root"),
        "ssh_key_path": pbs.get("ssh_key_path", "/app/data/id_ed25519"),
        # The External-mode watch timeouts were global in 0.9; they describe how slow this
        # particular PBS is, so they belong to the device.
        "external": {
            "first_task_wait": external.get("first_task_wait", 900),
            "idle_wait": external.get("idle_wait", 300),
        },
    }


def _device_id(preferred: Any, host: Any, fallback: str) -> str:
    """Pick a slug id: the node name if 0.9 knew one, else the host's first label, else a
    generic fallback. A bare IP makes a legal but unreadable id, so it is skipped."""
    for candidate in (preferred, _host_label(host)):
        slug = _slug(candidate)
        if slug:
            return slug
    return fallback


def _host_label(host: Any) -> str:
    text = str(host or "").split(":")[0].strip()
    if not text or _IPV4.match(text):
        return ""
    return text.split(".")[0]


def _slug(value: Any) -> str:
    text = _SLUG_STRIP.sub("-", str(value or "").strip().lower())
    return _SLUG_LEAD.sub("", text)


# --- routes -------------------------------------------------------------------


def _routes(
    backup: dict[str, Any],
    maintenance: dict[str, Any],
    pves: list[dict[str, Any]],
    pbss: list[dict[str, Any]],
    pve_id: str,
    pbs_id: str,
) -> list[dict[str, Any]]:
    if not pbss:  # every route needs a target PBS
        return []
    managed = pbss[0]["managed_power"]
    external_mode = bool(_section(backup, "external").get("enabled"))
    routes = []

    if external_mode:
        # Joulenap only wakes the PBS and watches the jobs PVE/PBS schedule themselves,
        # so there is no source and it starts no GC of its own.
        if managed:
            routes.append(_route(backup, "backup", "Backup", "external", pbs_id, gc=False))
        else:
            log.warning(
                "config: external-schedules mode needs a PBS Joulenap can wake, but '%s' "
                "has no MAC — no route was created for it",
                pbs_id,
            )
    elif pves and pves[0]["storages"].get(pbs_id):
        route = _route(
            backup,
            "backup",
            "Backup",
            "backup",
            pbs_id,
            gc=bool(_section(maintenance, "gc").get("enabled", True)),
        )
        route["sources"] = [{"pve": pve_id, "guests": _guests(_section(backup, "guests"))}]
        route["retention"] = dict(_section(backup, "retention"))
        route["options"]["verify_after"] = bool(
            _section(maintenance, "verify").get("after_backup", False)
        )
        routes.append(route)
    elif pves:
        log.warning(
            "config: pve '%s' has no storage configured for pbs '%s' — no backup route "
            "was created; add one from the UI",
            pve_id,
            pbs_id,
        )

    verify = _section(maintenance, "verify")
    if verify.get("enabled"):
        # 0.9's scheduled verification had its own wake/verify/power-off cycle: a route.
        route = _route(verify, "verify", "Verify", "verify", pbs_id, gc=False)
        route["color"] = VERIFY_ROUTE_COLOR
        route["options"]["reverify_days"] = verify.get("reverify_days", 30)
        routes.append(route)
    return routes


def _route(
    source: dict[str, Any],
    route_id: str,
    name: str,
    kind: str,
    target: str,
    *,
    gc: bool,
) -> dict[str, Any]:
    return {
        "id": route_id,
        "name": name,
        "color": BACKUP_ROUTE_COLOR,
        "enabled": bool(source.get("enabled", True)),
        "kind": kind,
        "target": target,
        "schedule": parse_cron(source.get("schedule", "")),
        "options": {
            "mode": source.get("mode", "snapshot"),
            "bwlimit": source.get("bwlimit", 0),
            "min_free_percent": source.get("min_free_percent", 0),
            "gc": gc,
        },
    }


def _guests(guests: dict[str, Any]) -> dict[str, Any]:
    mode = guests.get("mode", "all")
    if mode == "exclude":
        # 1.0 has no exclude mode. Inverting the list would need a live guest list we do
        # not have at load time, so widen to "all" — never silently drop guests from a
        # backup — and say so.
        log.warning(
            "config: guest mode 'exclude' no longer exists and became 'all', so guests %s "
            "are now included; narrow the route down from the UI if that is wrong",
            guests.get("list", []),
        )
        return {"mode": "all", "list": []}
    if mode == "include":
        return {"mode": "include", "list": list(guests.get("list", []))}
    return {"mode": "all", "list": []}


# --- cron ---------------------------------------------------------------------


def parse_cron(schedule: Any) -> dict[str, Any]:
    """Convert a 0.9 cron string into a route schedule.

    Returns ``{"time": ..., "days": [...]}`` when the cron is a plain "at HH:MM on these
    weekdays", which is all the route form can express. Anything richer — a day-of-month or
    month pattern, a step value, a weekday range — is kept verbatim as ``{"cron": ...}``
    rather than approximated into a schedule that would run at the wrong times.
    """
    fields = str(schedule or "").split()
    if len(fields) != 5:
        return {"cron": schedule} if fields else {}
    minute, hour, dom, month, dow = fields
    raw = {"cron": " ".join(fields)}
    if dom != "*" or month != "*":
        return raw
    if not (_INT.match(minute) and _INT.match(hour)):
        return raw
    if not (0 <= int(minute) <= 59 and 0 <= int(hour) <= 23):
        return raw
    days = _parse_dow(dow)
    if days is None:
        return raw
    return {"time": f"{int(hour):02d}:{int(minute):02d}", "days": days}


def _parse_dow(dow: str) -> List[bool] | None:  # noqa: UP006
    if dow == "*":
        return [True] * 7
    days = [False] * 7
    for token in dow.split(","):
        if not _INT.match(token) or int(token) > 7:
            return None  # a range or a name — not expressible as toggles
        days[_CRON_DOW_TO_INDEX[int(token)]] = True
    return days if any(days) else None


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}
