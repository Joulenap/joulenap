import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './i18n'
import './index.css'
import './responsive.css'

if (import.meta.env.VITE_STUB_API === '1') {
  await import('./devStub')
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
