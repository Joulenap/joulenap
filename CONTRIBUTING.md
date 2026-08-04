# Contributing to Joulenap

Thanks for your interest in improving Joulenap! This guide covers the local dev setup, how to run
the checks CI runs, and the conventions we follow.

By contributing you agree that your contributions are licensed under the project's
[AGPL-3.0](LICENSE).

## Project layout

- `backend/` — Python 3.12 + FastAPI + APScheduler. The app package is `backend/app`.
- `frontend/` — React + TypeScript SPA (Vite). Built output is served by the backend.
- `docs/` — design and setup docs. Start with [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) (the route
  model, the queue and lease, the REST API) and [`CONFIG-WIZARD.md`](docs/CONFIG-WIZARD.md) (the two
  device flows). [`INSTALL.md`](docs/INSTALL.md) covers deployment and
  [`INTEGRATIONS.md`](docs/INTEGRATIONS.md) the dashboard and Prometheus endpoints.

## Dev setup

You need **Python 3.12+** and **Node 24**.

Node 24 is not a suggestion: `npm test` runs `node --test` directly over `.ts` files using Node's
type-stripping, so it does not run at all on Node 20. CI and the Docker image are both pinned to 24,
and a CI job fails the build if those two ever drift apart.

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

ruff check .    # lint
pytest          # tests
```

### Frontend

```bash
cd frontend
npm ci
npm run dev      # Vite dev server (proxies /api to the backend on :8080)
npm run build    # type-check (tsc --noEmit) + production build
npm test         # node --test over the pure-logic modules
```

The test harness has **no DOM**, so anything worth asserting lives in a plain module under
`src/utils/` (route form rules, topology geometry, wizard flow) rather than inside a component.
Logic put in a component is untestable here by construction.

For a running app, copy the config first and start the backend:

```bash
cp config.example.yaml config.yaml
cd backend && python -m app.main    # serves the API (and the built SPA if you ran `npm run build`)
```

The example config ships **unconfigured**, so the app drops you into the first-run registration and
then a banner pointing at Settings → Devices, where the two wizards add a Proxmox host and a backup
server. Routes are drawn from the homepage once at least one of each exists. No real Proxmox is
needed to click around the UI.

### Frontend without a backend

To work on the UI alone — layout, styling, i18n — you can run the SPA against a built-in
stub instead of a real backend and a real Proxmox:

```bash
cd frontend
npm run dev -- --mode stub
```

`frontend/src/devStub.ts` answers every `/api/*` request from fixtures — three Proxmox hosts (one a
two-node cluster), two backup servers, three routes covering backup, a fan-in and a PBS→PBS sync,
run history including a failure and an aborted run, and enough wizard responses to click both device
flows end to end. It also **pins the clock**, so the UI renders identically on every run — which is
what makes it useful for screenshots and layout comparisons. Route and device writes really mutate
the fixtures (with the real 409s), so a route you create shows up in the strip and the topology.

It is loaded only when `VITE_STUB_API=1`, which `frontend/.env.stub` sets for the `stub` mode, and
Vite eliminates it from production builds.

Add `--host 0.0.0.0` to reach the dev server from a phone on the same network.

### The public demo

`npm run build:demo` bundles that same stub into `frontend/dist-demo/`, which is what the demo at
joulenap.com/demo serves. On top of the stub it adds a replay, driven by the scripted arcs in
`frontend/src/demoTimeline.ts`:

- a **moving clock**, and the whole fixture calendar shifted forward by a whole number of **weeks**
  — whole weeks so every weekday and time-of-day survives untouched, which is what lets the
  schedules, the upcoming-runs list and the history stay consistent with no scheduler in the stub;
- an orange **banner** saying the data is fake;
- the run **auto-plays on load**: you land on the Nightly route already mid-backup with its task log
  streaming, it finishes, and the queued Lab route then starts by itself while the backup server
  stays awake between the two — the run queue and the power lease, without a click. Run now, Stop
  and the per-server GC/verify buttons all drive the same machinery.

Everything in the replay sits behind `VITE_DEMO`, so `--mode stub` stays frozen and deterministic —
if you touch `devStub.ts`, keep it that way or dev screenshots stop being reproducible.

`--mode demo` is the only build that ships the stub; `npm run build` — what CI and the Docker image
run — never does. To check a change to it, build and serve the output rather than trusting the
build alone:

```bash
npm run build:demo
npx vite preview --outDir dist-demo
```

## Before you open a PR

Run what CI runs (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)); all of it must pass:

- Backend: `ruff check .` and `pytest` (CI runs the suite on Python 3.12 **and** 3.13).
- Frontend: `npm run build` (this type-checks with `tsc --noEmit`) **and `npm test`**. Both are
  separate CI steps — a green build is not a green suite.

CI also runs dependency (pip-audit / npm audit) and Docker image (Trivy) security scans.

## Conventions

- **Small, reviewable commits**; work on a feature branch and open a PR against `main`.
- **Tests** for the connectors and backup-cycle logic — that's where correctness matters most.
- **Keep it config-driven** — nothing hard-coded (no specific IPs/MACs in code); validate config
  with pydantic and fail clearly.
- **Secrets** never get committed. `config.yaml` is git-ignored; don't add real hosts/tokens/MACs to
  code, tests, or docs — use placeholder/`192.0.2.x` (TEST-NET) values.
- **i18n**: user-facing UI strings go through `t('key')` with entries in **both**
  `frontend/src/i18n/en.json` and `frontend/src/i18n/it.json` (English is the source language).
  Backend-facing strings (errors, notifications) use the server-side dictionary. Never concatenate
  translated strings — use interpolation.

## Reporting bugs and requesting features

Use the issue templates (bug report / feature request). For **security vulnerabilities**, do not
open a public issue — follow [`SECURITY.md`](SECURITY.md) instead.
