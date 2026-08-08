import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { yaml as yamlLang } from '@codemirror/lang-yaml'
import { HighlightStyle, indentUnit, syntaxHighlighting } from '@codemirror/language'
import { setDiagnostics } from '@codemirror/lint'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers } from '@codemirror/view'
import { tags as tg } from '@lezer/highlight'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Spinner } from '../../components/Spinner'
import { useConfig } from '../../config/ConfigContext'
import { useRegisterDirty } from '../../shell/UnsavedGuard'
import { c, currentTheme, mono } from '../../theme'
import { copyToClipboard } from '../../utils/clipboard'

// This module is loaded lazily (see Advanced.tsx) so CodeMirror lands in its own chunk and
// never weighs on the dashboard's first paint.

// Colors are CSS vars, so a theme toggle restyles the editor live; the `dark` flag only
// scopes CodeMirror's own defaults and is read at mount (stale until remount — acceptable).
const makeTheme = () =>
  EditorView.theme(
    {
      '&': { height: '440px', fontSize: '13px', color: c.text },
      '&.cm-focused': { outline: `1px solid ${c.accent}` },
      '.cm-scroller': { fontFamily: mono, overflow: 'auto' },
      '.cm-content': { caretColor: c.accent },
      '.cm-cursor': { borderLeftColor: c.accent },
      '.cm-gutters': { background: c.panelAlt, color: c.textMuted, border: 'none' },
      '.cm-activeLine': { background: c.hover },
      '.cm-activeLineGutter': { background: 'transparent', color: c.textDim },
      '&.cm-focused .cm-selectionBackground, ::selection': { background: 'rgba(232,131,15,.25)' },
    },
    { dark: currentTheme() === 'dark' },
  )

const highlight = HighlightStyle.define([
  { tag: [tg.propertyName, tg.definition(tg.propertyName)], color: c.accent },
  { tag: tg.string, color: c.green },
  { tag: [tg.number, tg.bool, tg.null], color: c.blue },
  { tag: tg.comment, color: c.textMuted, fontStyle: 'italic' },
  { tag: [tg.punctuation, tg.separator], color: c.textDim },
])

export function YamlEditor() {
  const { t } = useTranslation()
  const { reload } = useConfig()
  const host = useRef<HTMLDivElement | null>(null)
  const view = useRef<EditorView | null>(null)

  // `saved` is the last text the server accepted; `text` tracks the buffer so the Apply
  // button and the unsaved-changes guard know when they differ.
  const [saved, setSaved] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<'saved' | 'copied' | 'copyFailed' | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  const dirty = saved !== null && text !== saved
  useRegisterDirty(dirty)

  useEffect(() => {
    api
      .getConfigYaml()
      .then(({ yaml }) => {
        setSaved(yaml)
        setText(yaml)
      })
      .catch((e) => setLoadErr(e instanceof ApiError ? e.message : String(e)))
  }, [])

  // Mount CodeMirror once the initial document is in hand. The view is uncontrolled: React
  // never pushes `text` back into it, it only mirrors what the user types.
  useEffect(() => {
    if (saved === null || !host.current || view.current) return
    view.current = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: saved,
        extensions: [
          lineNumbers(),
          history(),
          // No indentWithTab: Tab keeps moving focus, so the editor stays keyboard-escapable.
          keymap.of([...defaultKeymap, ...historyKeymap]),
          indentUnit.of('  '),
          yamlLang(),
          syntaxHighlighting(highlight),
          makeTheme(),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) {
              setText(u.state.doc.toString())
              setErr(null)
              setNote(null)
            }
          }),
        ],
      }),
    })
    return () => {
      view.current?.destroy()
      view.current = null
    }
  }, [saved])

  function mark(message: string, line: number | null) {
    const v = view.current
    if (!v) return
    if (line === null) {
      v.dispatch(setDiagnostics(v.state, []))
      return
    }
    const l = v.state.doc.line(Math.min(Math.max(line, 1), v.state.doc.lines))
    v.dispatch(setDiagnostics(v.state, [{ from: l.from, to: l.to, severity: 'error', message }]))
  }

  async function onApply() {
    const current = view.current?.state.doc.toString() ?? text
    setBusy(true)
    setErr(null)
    try {
      await api.putConfigYaml(current)
      setSaved(current)
      setNote('saved')
      mark('', null)
      await reload() // keep the other tabs' shared config in sync
    } catch (e) {
      if (e instanceof ApiError) {
        setErr(e.message)
        const line = (e.raw as { line?: number } | undefined)?.line
        mark(e.message, typeof line === 'number' ? line : null)
      } else {
        setErr(String(e))
      }
    } finally {
      setBusy(false)
    }
  }

  async function onCopy() {
    const ok = await copyToClipboard(view.current?.state.doc.toString() ?? text)
    setNote(ok ? 'copied' : 'copyFailed')
  }

  /**
   * Download the document as a file. The buffer is used rather than a fresh GET so what
   * lands on disk is what is on screen, unsaved edits included.
   *
   * It is the *redacted* document — `GET /config/yaml` masks every secret — so this is a
   * readable copy for reference or a diff, not a restorable backup. The help text says so.
   */
  function onExport() {
    const blob = new Blob([view.current?.state.doc.toString() ?? text], {
      type: 'application/yaml',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'config.yaml'
    a.click()
    URL.revokeObjectURL(url)
  }

  const ns = 'settings.advanced'

  return (
    <section className="panel">
      <div className="panel-hd">
        <h2>config.yaml</h2>
        <div className="panel-actions">
          <span className="panel-hint">{t(`${ns}.yamlSubtitle`)}</span>
          <button type="button" className="btn btn-sm" onClick={onExport} disabled={saved === null}>
            ⭳ {t(`${ns}.export`)}
          </button>
        </div>
      </div>
      <div className="panel-bd stack tight">
        {loadErr && <span className="err-note">{loadErr}</span>}
        {saved === null && !loadErr && <Spinner />}

        <div
          ref={host}
          style={{
            display: saved === null ? 'none' : 'block',
            background: c.inputBg,
            border: `1px solid ${c.inputBorder}`,
            borderRadius: 8,
            overflow: 'hidden',
          }}
        />

        <span className="help">{t(`${ns}.yamlHint`)}</span>
        <span className="help">{t(`${ns}.exportHint`)}</span>

        <div className="save-row">
          <button type="button" className="btn" style={{ marginRight: 'auto' }} onClick={() => void onCopy()}>
            {t(`${ns}.copy`)}
          </button>
          {note === 'saved' && !dirty && <span className="ok-note">{t(`${ns}.yamlSaved`)}</span>}
          {note === 'copied' && <span className="ok-note">{t(`${ns}.copied`)}</span>}
          {note === 'copyFailed' && <span className="err-note">{t(`${ns}.copyFailed`)}</span>}
          {dirty && <span className="help">{t('common.unsavedChanges')}</span>}
          <button
            type="button"
            className="btn btn-accent"
            disabled={!dirty || busy}
            onClick={() => void onApply()}
          >
            {t(`${ns}.yamlApply`)}
          </button>
        </div>

        {err && (
          <pre
            role="alert"
            style={{ fontSize: 12.5, color: c.red, fontFamily: mono, whiteSpace: 'pre-wrap', margin: 0 }}
          >
            {err}
          </pre>
        )}
      </div>
    </section>
  )
}

export default YamlEditor
