import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './i18n'
import './index.css'
import './responsive.css'

// The stub fakes an authenticated session, so exactly two things may load it:
//   - dev server with `--mode stub` (`import.meta.env.DEV` is false for any `vite build`);
//   - `npm run build:demo`, i.e. `--mode demo`, which builds the public joulenap.com/demo
//     page and nothing else. `Dockerfile` and CI both run `npm run build` (default mode),
//     where VITE_DEMO is undefined and Vite's static replacement drops the import entirely.
if (import.meta.env.VITE_DEMO === '1' || (import.meta.env.DEV && import.meta.env.VITE_STUB_API === '1')) {
  await import('./devStub')
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
