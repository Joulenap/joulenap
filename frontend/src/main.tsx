import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './i18n'
import './index.css'
import './responsive.css'

// `import.meta.env.DEV` is false for `vite build` under every mode, so no build can ship the
// stub — not even `vite build --mode stub`, which would otherwise load `.env.stub` and bundle a
// module that fakes an authenticated session.
if (import.meta.env.DEV && import.meta.env.VITE_STUB_API === '1') {
  await import('./devStub')
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
