# Dashboard integration

Joulenap exposes a small, read-only, API-key-protected JSON endpoint —
`GET /api/dashboard` — so a homelab dashboard (Homepage, Homarr, Dashy,
Glance, …) can poll it and show your backup status alongside your other
services: whether the PBS is asleep/awake/backing up, when the next run is
scheduled, how the last run went, and how full the datastore is.

This endpoint is intentionally separate from the internal `/api/status` used
by Joulenap's own UI: it's a stable, additive-only public contract with
plain machine-readable values (no localization, no session cookie), guarded
by its own API key instead of a login session.

## Enabling it

1. Open Joulenap → **Settings → Integrations**.
2. Click **Generate API key**. The key is shown once — copy it somewhere
   safe (a password manager, your dashboard's secret store, etc.). Joulenap
   only stores a copy needed to verify requests; it won't show you the key
   again.
3. Pick your dashboard in the picker on that page to get a ready-to-paste
   config snippet with the key and endpoint URL already filled in.
4. Disabling the integration (the **Disable** button) clears the key and the
   endpoint immediately starts rejecting requests again.

Regenerating the key invalidates the previous one immediately — update any
dashboard config that used the old key.

## Authentication

Every request to `/api/dashboard` must include the API key, either as:

- an **`X-API-Key` header** (preferred, wherever the dashboard supports
  custom request headers), or
- a **`?key=<your-api-key>` query parameter** appended to the URL, for
  dashboards/widgets that can't set custom headers.

No key configured → `403 Forbidden` (integration disabled). Wrong or missing
key → `401 Unauthorized`.

## Response reference

`GET /api/dashboard` returns a flat JSON object:

| Field | Type | Values / meaning |
|-------|------|------------------|
| `pbs_state` | string | `sleeping` \| `online` \| `backing_up` |
| `next_run` | string \| null | ISO 8601 next scheduled backup; null if scheduler disabled |
| `last_run_status` | string | `success` \| `failed` \| `never` |
| `last_run_time` | string \| null | ISO 8601 of the last backup cycle; null if none |
| `datastore_used_pct` | number \| null | Percent used; null if PBS off / probe failed |
| `datastore_used_bytes` | number \| null | Bytes used |
| `datastore_total_bytes` | number \| null | Total bytes (free = total − used) |

## Per-dashboard setup

The endpoint URL is your Joulenap instance's origin plus `/api/dashboard`,
e.g. `http://192.168.1.50:8080/api/dashboard`. Replace `<your-api-key>` with
the key from step 2 above in every snippet below.

### Homepage

Homepage's built-in `customapi` widget maps JSON response fields directly
onto labelled rows:

```yaml
- Joulenap:
    icon: http://192.168.1.50:8080/favicon.svg
    href: http://192.168.1.50:8080
    widget:
      type: customapi
      url: http://192.168.1.50:8080/api/dashboard
      headers:
        X-API-Key: <your-api-key>
      mappings:
        - field: pbs_state
          label: PBS
        - field: next_run
          label: Next backup
          format: relativeDate
        - field: last_run_status
          label: Last run
        - field: datastore_used_pct
          label: Datastore
          format: percent
```

### Glance

Glance's `custom-api` widget fetches the JSON and renders it through a Go
template:

```yaml
- type: custom-api
  title: Joulenap
  url: http://192.168.1.50:8080/api/dashboard
  headers:
    X-API-Key: <your-api-key>
  template: |
    <div>PBS: {{ .JSON.String "pbs_state" }}</div>
    <div>Next: {{ .JSON.String "next_run" }}</div>
    <div>Last run: {{ .JSON.String "last_run_status" }}</div>
    <div>Datastore: {{ .JSON.Int "datastore_used_pct" }}%</div>
```

### Homarr

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
> - Map the fields you want to show: `pbs_state`, `next_run`,
>   `last_run_status`, `last_run_time`, `datastore_used_pct`,
>   `datastore_used_bytes`, `datastore_total_bytes`
>
> If your version can't set a custom header at all, use the query-string
> fallback for the URL field instead:
> `http://192.168.1.50:8080/api/dashboard?key=<your-api-key>`

### Dashy

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
      - field: pbs_state
        label: PBS
      - field: next_run
        label: Next backup
        format: relativeDate
      - field: last_run_status
        label: Last run
      - field: datastore_used_pct
        label: Datastore
        format: percent
```

If your self-hosted Joulenap doesn't send CORS headers and the widget fails
to fetch, set the widget-level `useProxy: true` so Dashy fetches server-side
instead of from the browser. If your Dashy version predates the `customapi`
widget, use the query-string fallback
(`http://192.168.1.50:8080/api/dashboard?key=<your-api-key>`) with whatever
generic widget your version offers.
