import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './en.json' with { type: 'json' }
import it from './it.json' with { type: 'json' }

// English is the source/base language. The active language follows
// app.language from the backend config; AuthGate calls i18n.changeLanguage once loaded.
i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    it: { translation: it },
  },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

export default i18n
