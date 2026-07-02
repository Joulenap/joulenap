# Joulenap frontend

React + TypeScript + Vite SPA for Joulenap, recreated from the prototypes in
`design/joulenap-remix/` and wired to the backend REST API.

## Develop

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173, proxies /api -> http://localhost:8080
```

Run the backend (`cd backend && joulenap` or `uvicorn app.main:create_app --factory`)
alongside it so the proxied API is available.

## Build

```bash
npm run build      # type-checks (tsc --noEmit) then emits frontend/dist
```

The FastAPI backend serves `frontend/dist` as static files, so a production image just
needs the built output present. `dist/` is git-ignored.

## Layout

- `src/api/` — typed client + response types for `/api/*`
- `src/auth/`, `src/config/` — auth + config React contexts
- `src/pages/` — `Login`, `Dashboard` (+ `dashboard/` panels), `Settings` (+ `settings/` panels incl. the setup wizard)
- `src/shell/` — header + authenticated app shell
- `src/components/`, `src/hooks/`, `src/utils/` — shared widgets/helpers
- `src/i18n/` — `react-i18next` setup; `en.json` is the base language, `it.json` the translation

## i18n

English is the source language. The active language follows `app.language` from the
backend config (the Localization settings panel changes it). Add a key to `en.json`
first, then mirror it in `it.json`.
