# Grafana dashboard

`joulenap-overview.json` is a ready-made Grafana dashboard for the metrics Joulenap exposes on
[`GET /metrics`](../INTEGRATIONS.md#prometheus--grafana). It covers the whole install at a glance:
build/version, scheduler and job state, the recent-runs breakdown, per-backup-server health
(online, CPU, memory, uptime), datastore usage and capacity, route run status, and each guest's
last backup time.

## Prerequisites

1. The Prometheus integration enabled under **Settings → Integrations** (see
   [Enabling it](../INTEGRATIONS.md#enabling-it)), and a Prometheus scraping `/metrics` per the
   [scrape config](../INTEGRATIONS.md#scrape-config).
2. That Prometheus added as a data source in Grafana.
3. **Grafana 12.2+.** The file uses Grafana's newer dashboard schema
   (`apiVersion: dashboard.grafana.app/v2`) rather than the classic `schemaVersion`/`panels`
   format. If your Grafana is older, or "Import → paste JSON" rejects the file, update Grafana
   rather than hand-editing the schema.

## Importing

**Dashboards → New → Import**, paste the contents of `joulenap-overview.json` (or upload the
file), then pick your Prometheus data source when prompted — the dashboard uses a `${datasource}`
variable rather than a hard-coded one, so it isn't tied to any particular data source name or UID.

Two more template variables filter everything below the top row:

- **PBS** — backup servers, from `label_values(joulenap_pbs_online, pbs)`
- **Route** — routes, from `label_values(joulenap_route_last_run_success, route)`

Both default to "All" and populate automatically from whatever your instance reports; nothing to
edit per install.

## Layout

- **Overview** — version, scheduler enabled, job running, queued runs, recent runs by status
- **PBS Nodes** — online status, CPU %, memory %, uptime per backup server
- **Datastores** *(collapsed by default)* — usage % and used/total capacity
- **Routes** *(collapsed by default)* — last/next run time, last run success, duration, guest count
- **Guest Backups** *(collapsed by default)* — last backup time per guest

The last three rows start collapsed since they scale with your config (more routes, more guests)
rather than being fixed at a handful of panels.
