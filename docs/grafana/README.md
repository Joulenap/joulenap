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
3. Any current Grafana. The file is in Grafana's classic dashboard format, so it imports
   without feature toggles (tested on 12.2, 12.4 and 13.2).

## Importing

**Dashboards → New → Import**, paste the contents of `joulenap-overview.json` (or upload the
file) and confirm. The dashboard reads its data source from a `${datasource}` variable rather than
a hard-coded one, so it isn't tied to any particular data source name or UID: it starts on your
default Prometheus, and the **Datasource** dropdown at the top switches it.

Two more template variables filter everything below the top row:

- **PBS**: backup servers, from `label_values(joulenap_pbs_online, pbs)`
- **Route**: routes, from `label_values(joulenap_route_last_run_success, route)`

Both default to "All" and populate automatically from whatever your instance reports; nothing to
edit per install.

## Layout

- **Overview**: version, scheduler enabled, job running, queued runs, recent runs by status
- **PBS Nodes**: online status, CPU %, memory %, uptime per backup server
- **Datastores** *(collapsed by default)*: usage % and used/total capacity
- **Routes** *(collapsed by default)*: last/next run time, last run success, duration, guest count
- **Guest Backups** *(collapsed by default)*: last backup time per guest

The last three rows start collapsed since they scale with your config (more routes, more guests)
rather than being fixed at a handful of panels.
