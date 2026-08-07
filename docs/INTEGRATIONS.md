# Integrations

<details>
<summary><b>Upgrading from 0.9?</b> Both payloads changed shape — the field-by-field 0.9 → 1.0 mapping is here</summary>

> **Breaking in 1.0.** Both payloads changed shape, because a Joulenap install
> now has several routes and several backup servers — there is no single "next
> run" or "the datastore" left to report. Update any widget or query built on
> 0.9:
>
> | 0.9 | 1.0 |
> |---|---|
> | `pbs_state` | `pbss[].state` (`pbss.0.state` for the first box) |
> | `next_run` | `routes[].next_run` |
> | `last_run_status` / `last_run_time` | `routes[].last_run_status` / `.last_run_time` |
> | `datastore_used_pct` / `_used_bytes` / `_total_bytes` | `pbss[].datastore_used_pct` / … |
> | — | `state` (the app's own idle/running/paused) is new and still top-level |
> | `joulenap_next_run_timestamp_seconds` | `joulenap_route_next_run_timestamp_seconds{route=}` |
> | `joulenap_last_run_*` | `joulenap_route_last_run_*{route=}` |
> | `joulenap_pbs_*`, `joulenap_datastore_*` | same names, now labelled `{pbs=}` |
> | `joulenap_guest_last_backup_timestamp_seconds{vmid}` | `{vmid, pve, pbs}` |
>
> Settings → Integrations always shows a snippet generated for the version you
> are running; if in doubt, copy from there rather than from this page.

</details>

Joulenap exposes two read-only, API-key-protected endpoints for other tools:

- **`GET /api/dashboard`** — a flat JSON summary for homelab dashboards
  (Homepage, Homarr, Dashy, Glance). See below.
- **`GET /metrics`** — Prometheus metrics for Grafana. See
  [Prometheus & Grafana](#prometheus--grafana).

Both use the **same API key**, generated once under **Settings →
Integrations**; enabling the integration enables both.

## Enabling it

1. Open Joulenap → **Settings → Integrations**.
2. Click **Generate API key**. The key is shown once — copy it somewhere
   safe (a password manager, your dashboard's secret store, etc.). Joulenap
   only stores a copy needed to verify requests; it won't show you the key
   again.
3. Pick your dashboard in the picker on that page to get a ready-to-paste
   config snippet with the key and endpoint URL already filled in.
4. Disabling the integration (the **Disable** button) clears the key, and
   both endpoints immediately start rejecting requests again.

Regenerating the key invalidates the previous one immediately — update any
dashboard or scrape config that used the old key.

## Authentication

Every request to `/api/dashboard` and `/metrics` must include the API key,
either as:

- an **`X-API-Key` header** (preferred, wherever the client supports custom
  request headers), or
- a **`?key=<your-api-key>` query parameter** appended to the URL, for
  dashboards/widgets that can't set custom headers — and for Prometheus,
  whose `params:` setting works on every version.

No key configured → `403 Forbidden` (integration disabled). Wrong or missing
key → `401 Unauthorized`.

## Dashboard integration

`GET /api/dashboard` lets a homelab dashboard poll Joulenap and show your
backup status alongside your other services: whether each backup server is
asleep/awake/backing up, when each route next runs, how its last run went,
and how full each datastore is.

This endpoint is intentionally separate from the internal `/api/status` used
by Joulenap's own UI: it's a stable, additive-only public contract with
plain machine-readable values (no localization, no session cookie), guarded
by its own API key instead of a login session.

### Response reference

`GET /api/dashboard` returns one top-level field and two lists — one entry
per route, one per backup server:

```json
{
  "state": "running",
  "routes": [
    {
      "id": "nightly",
      "name": "Nightly",
      "kind": "backup",
      "enabled": true,
      "next_run": "2026-07-10T02:00:00Z",
      "last_run_status": "success",
      "last_run_time": "2026-07-09T02:00:00Z"
    }
  ],
  "pbss": [
    {
      "id": "pbs-01",
      "state": "backing_up",
      "datastore_used_pct": 62,
      "datastore_used_bytes": 1900000000000,
      "datastore_total_bytes": 3100000000000
    }
  ]
}
```

| Field | Type | Values / meaning |
|-------|------|------------------|
| `state` | string | `idle` \| `running` \| `paused` — what *Joulenap* is doing, not a backup server |
| `routes[].id` / `.name` | string | the route's id, and the name you gave it (falls back to the id) |
| `routes[].kind` | string | `backup` \| `sync` \| `external` \| `verify` |
| `routes[].enabled` | boolean | false for a route you paused |
| `routes[].next_run` | string \| null | ISO 8601; null when the route is not armed (disabled, or the kill-switch is off) |
| `routes[].last_run_status` | string | `success` \| `failed` \| `never` |
| `routes[].last_run_time` | string \| null | ISO 8601 of that run's start; null if it never ran |
| `pbss[].id` | string | the backup server's id |
| `pbss[].state` | string | `sleeping` \| `online` \| `backing_up` (a run is holding its power lease) |
| `pbss[].datastore_used_pct` | number \| null | percent used; null if never probed |
| `pbss[].datastore_used_bytes` | number \| null | bytes used — served from cache while the box sleeps |
| `pbss[].datastore_total_bytes` | number \| null | total bytes (free = total − used) |

Both lists follow your configuration order, so `routes.0` is your first
configured route — not "the" route. A widget that only has room for one line
should pick a specific entry; one that can iterate should range over the list.

### Per-dashboard setup

The endpoint URL is your Joulenap instance's origin plus `/api/dashboard`,
e.g. `http://192.168.1.50:8080/api/dashboard`. Replace `<your-api-key>` with
the key from step 2 above in every snippet below.

<details>
<summary><b>Homepage — the built-in <code>customapi</code> widget</b></summary>

Homepage's built-in `customapi` widget maps JSON response fields directly
onto labelled rows:

```yaml
- Joulenap:
    icon: http://192.168.1.50:8080/assets/joulenap-icon.svg
    href: http://192.168.1.50:8080
    widget:
      type: customapi
      url: http://192.168.1.50:8080/api/dashboard
      headers:
        X-API-Key: <your-api-key>
      mappings:
        - field: state
          label: Joulenap
        - field: routes.0.name
          label: Route
        - field: routes.0.next_run
          label: Next run
          format: relativeDate
        - field: routes.0.last_run_status
          label: Last run
        - field: pbss.0.datastore_used_pct
          label: Datastore
          format: percent
# .0 is the first configured route / backup server — add more mappings for the rest.
```

</details>

<details>
<summary><b>Glance — the <code>custom-api</code> widget with a Go template</b></summary>

Glance's `custom-api` widget fetches the JSON and renders it through a Go
template:

```yaml
- type: custom-api
  title: Joulenap
  url: http://192.168.1.50:8080/api/dashboard
  headers:
    X-API-Key: <your-api-key>
  template: |
    <div>State: {{ .JSON.String "state" }}</div>
    {{ range .JSON.Array "routes" }}
      <div>{{ .String "name" }}: {{ .String "last_run_status" }}
           (next {{ .String "next_run" }})</div>
    {{ end }}
    {{ range .JSON.Array "pbss" }}
      <div>{{ .String "id" }}: {{ .String "state" }}
           — {{ .Int "datastore_used_pct" }}%</div>
    {{ end }}
```

Glance ranges over both lists, so it grows with your config on its own — no
index to pick and nothing to edit when you add a route or a second backup
server.

</details>

<details>
<summary><b>Homarr — the Custom API widget (v1.65+, configured in the dashboard)</b></summary>

> **Note:** Homarr's widget system changed significantly in 2026. Older
> Homarr releases only offered a generic iframe/link-style widget with no
> real JSON field mapping. As of the "Custom Widgets" feature (Homarr
> v1.65+), there is a proper, dashboard-managed **Custom API widget** — no
> YAML file to edit. If you're on an older version, upgrade for the field
> mapping described below; otherwise fall back to an iframe/link widget
> pointed at the `?key=` URL.
>
> Configure it under **Management → Custom Widgets → Add** (or from the
> dashboard's widget picker → *Custom API*, depending on version):
>
> - **URL**: `http://192.168.1.50:8080/api/dashboard`
> - **HTTP Method**: `GET`
> - **Authentication**: `API Key (Header)` → Header Name `X-API-Key`, value
>   `<your-api-key>` (use `API Key (Query)` instead if your Homarr version
>   only offers query-parameter auth, with parameter name `key`)
> - **Display Type**: `Key Value` (or `Custom JSX` for full control over
>   layout)
> - Map the fields you want to show:
>   ```
>   state                            idle | running | paused
>   routes[]  id, name, kind, enabled, next_run,
>             last_run_status, last_run_time
>   pbss[]    id, state, datastore_used_pct,
>             datastore_used_bytes, datastore_total_bytes
>   ```
>   Index into the lists — `routes.0.next_run` is the first configured
>   route's next run.
>
> If your version can't set a custom header at all, use the query-string
> fallback for the URL field instead:
> `http://192.168.1.50:8080/api/dashboard?key=<your-api-key>`

</details>

<details>
<summary><b>Dashy — the <code>customapi</code> widget</b></summary>

> **Note:** Dashy's generic JSON widget is called `customapi` (it was
> explicitly modeled after Homepage's widget of the same name), not a plain
> iframe. It supports request headers and the same kind of field mappings
> as Homepage:

```yaml
- type: customapi
  options:
    url: http://192.168.1.50:8080/api/dashboard
    headers:
      X-API-Key: <your-api-key>
    mappings:
      - field: routes.0.name
        label: Route
      - field: routes.0.next_run
        label: Next run
        format: relativeDate
      - field: routes.0.last_run_status
        label: Last run
      - field: pbss.0.datastore_used_pct
        label: Datastore
        format: percent
```

If your self-hosted Joulenap doesn't send CORS headers and the widget fails
to fetch, set the widget-level `useProxy: true` so Dashy fetches server-side
instead of from the browser. If your Dashy version predates the `customapi`
widget, use the query-string fallback
(`http://192.168.1.50:8080/api/dashboard?key=<your-api-key>`) with whatever
generic widget your version offers.

</details>

## Prometheus & Grafana

`GET /metrics` exposes Joulenap's state in the Prometheus text format, so a
homelab Prometheus can scrape it and Grafana can graph it — and, more to the
point, so Alertmanager can tell you **when a guest stops being backed up**.

It's served at `/metrics` (not under `/api`) because that's Prometheus's
default `metrics_path`.

A scrape reads the database and does the same one-second TCP probe the
dashboard uses. **It never wakes the PBS**, and datastore usage and per-guest
backup times come from Joulenap's cache, so they keep reporting while the
box is asleep — which is most of the time, by design.

### Scrape config

Prometheus's `params:` works on every version, unlike custom scrape headers:

```yaml
scrape_configs:
  - job_name: joulenap
    metrics_path: /metrics
    params:
      key: ["<your-api-key>"]
    static_configs:
      - targets: ["192.168.1.50:8080"]
```

A 60s `scrape_interval` is plenty — nothing here changes faster than a
backup cycle.

### Metric reference

All metrics are gauges prefixed `joulenap_`. **Almost everything is labelled**:
per-route series carry `route=`, per-box series carry `pbs=`.

| Metric | Labels | Meaning |
|--------|--------|---------|
| `joulenap_build_info` | `version` | Always 1; the label carries the running version |
| `joulenap_scheduler_enabled` | — | 1 if the kill-switch is on (routes may be armed) |
| `joulenap_job_running` | — | 1 while a backup, sync, GC or verify run is in flight |
| `joulenap_queued_runs` | — | Runs waiting behind the one in flight |
| `joulenap_pbs_online` | `pbs` | 1 if this backup server answers on its API port, 0 while asleep |
| `joulenap_pbs_cpu_percent` | `pbs` | CPU %, **only present while that box is awake** |
| `joulenap_pbs_memory_percent` | `pbs` | Memory %, only while awake |
| `joulenap_pbs_uptime_seconds` | `pbs` | Uptime, only while awake |
| `joulenap_datastore_used_bytes` | `pbs`, `datastore` | Datastore bytes used (last known value) |
| `joulenap_datastore_total_bytes` | `pbs`, `datastore` | Datastore size in bytes (last known value) |
| `joulenap_route_next_run_timestamp_seconds` | `route` | Unix time this route next fires; **absent when it isn't armed** |
| `joulenap_route_last_run_timestamp_seconds` | `route` | When this route's last finished run started |
| `joulenap_route_last_run_success` | `route` | 1 if it succeeded, 0 if it failed or was aborted |
| `joulenap_route_last_run_duration_seconds` | `route` | How long that run took |
| `joulenap_route_last_run_guests` | `route` | Guests backed up by that run |
| `joulenap_guest_last_backup_timestamp_seconds` | `vmid`, `pve`, `pbs` | Each guest's most recent snapshot |
| `joulenap_runs_recent` | `kind`, `status` | Finished runs in the history window |

`joulenap_guest_last_backup_timestamp_seconds` is labelled by the PVE the
guest lives on and the backup server holding the snapshot as well as the
vmid, because a vmid alone stopped being unique the moment a second PVE could
exist — and the same guest synced to two boxes is legitimately two series.

Two things worth knowing before you write queries:

- **A value Joulenap doesn't have is an absent series, not a zero.** A route
  that has never run has no `joulenap_route_last_run_timestamp_seconds`, and a
  disabled one has no `..._next_run_...` — publishing `0` would graph your last
  backup as January 1970. Use `absent()` to alert on "never ran".
- **`joulenap_runs_recent` is a gauge, not a counter.** The daily prune job
  deletes runs older than `maintenance.history.retention_days`, so the number
  legitimately goes *down* — `rate()` and `increase()` would be nonsense on
  it. It answers "how many failures are in my retention window", not "how
  many ever".

### Useful queries

```promql
# Hours since each guest was last backed up
(time() - joulenap_guest_last_backup_timestamp_seconds) / 3600

# Datastore usage percent, per backup server
100 * joulenap_datastore_used_bytes / joulenap_datastore_total_bytes

# Days until each datastore is full, from the last week's growth
(joulenap_datastore_total_bytes - joulenap_datastore_used_bytes)
  / (deriv(joulenap_datastore_used_bytes[7d]) * 86400)

# Share of recent backup cycles that succeeded
joulenap_runs_recent{kind="cycle",status="success"}
  / sum by () (joulenap_runs_recent{kind="cycle"})

# Routes whose last run failed
joulenap_route_last_run_success == 0

# Hours since each route last ran
(time() - joulenap_route_last_run_timestamp_seconds) / 3600
```

The run kinds you'll see on `joulenap_runs_recent` are `cycle` (a backup
route), `sync`, `monitor` (an external route's watch), `verify` and `gc`.

### Alerting rules

The one that justifies wiring this up at all — a guest quietly falling out
of your backup set:

```yaml
groups:
  - name: joulenap
    rules:
      - alert: JoulenapGuestBackupStale
        expr: time() - joulenap_guest_last_backup_timestamp_seconds > 172800
        for: 1h
        annotations:
          summary: >-
            Guest {{ $labels.vmid }} on {{ $labels.pve }} has no backup on
            {{ $labels.pbs }} in over 48h

      - alert: JoulenapRouteFailed
        expr: joulenap_route_last_run_success == 0
        for: 15m
        annotations:
          summary: "Route {{ $labels.route }} did not succeed on its last run"

      - alert: JoulenapRouteNeverRan
        expr: absent(joulenap_route_last_run_timestamp_seconds{route="nightly"})
        for: 24h
        annotations:
          summary: "Route nightly has never completed a run"

      - alert: JoulenapDatastoreFilling
        expr: 100 * joulenap_datastore_used_bytes / joulenap_datastore_total_bytes > 85
        for: 1h
        annotations:
          summary: "Datastore {{ $labels.datastore }} on {{ $labels.pbs }} is over 85% full"
```

`absent()` needs the series named in full, so the "never ran" alert has to
name the route — repeat the rule per route you care about, or template it out
of your own config.

Set the staleness threshold to comfortably more than your backup interval —
`172800` (48h) suits a nightly schedule; a run that starts late or takes a
while shouldn't page you.
