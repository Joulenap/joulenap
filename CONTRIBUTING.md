# Contributing to Joulenap

Thanks for your interest in improving Joulenap! This guide covers the local dev setup, how to run
the checks CI runs, and the conventions we follow.

By contributing you agree that your contributions are licensed under the project's
[AGPL-3.0](LICENSE).

## Project layout

- `backend/` — Python 3.12 + FastAPI + APScheduler. The app package is `backend/app`.
- `frontend/` — React + TypeScript SPA (Vite). Built output is served by the backend.
- `docs/` — design and setup docs. Start with [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
  [`CONFIG-WIZARD.md`](docs/CONFIG-WIZARD.md).

## Dev setup

You need Python 3.12+ and Node 20+.

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
```

For a running app, copy the config first and start the backend:

```bash
cp config.example.yaml config.yaml
cd backend && python -m app.main    # serves the API (and the built SPA if you ran `npm run build`)
```

The example config ships **unconfigured**, so the app drops you into the first-run registration and
setup wizard — no real Proxmox needed to click around the UI.

### Frontend without a backend

To work on the UI alone — layout, styling, i18n — you can run the SPA against a built-in
stub instead of a real backend and a real Proxmox:

```bash
cd frontend
npm run dev -- --mode stub
```

`frontend/src/devStub.ts` answers every `/api/*` request from fixtures (a configured install,
three guests, a few log lines) and pins the clock, so the UI renders exactly the same on every
run — handy for screenshots and layout comparisons. It is loaded only when `VITE_STUB_API=1`,
which `frontend/.env.stub` sets for the `stub` mode, and Vite eliminates it from production
builds.

Add `--host 0.0.0.0` to reach the dev server from a phone on the same network.

## Before you open a PR

Run what CI runs (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)); all of it must pass:

- Backend: `ruff check .` and `pytest` (CI runs the suite on Python 3.12 **and** 3.13).
- Frontend: `npm run build` (this type-checks with `tsc --noEmit`).

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
