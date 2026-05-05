<script setup lang="ts">
// SQL editor pane: CodeMirror 6 on top, results grid below, with a
// draggable horizontal splitter between them.
//
// Run rules (IDE standard):
//   - If a selection is active, run only the selection.
//   - Otherwise, run the entire document.
//   - Triggers: Run button, F5, Ctrl/Cmd+Enter.
// Esc cancels the running query via DuckDB's `interrupt()`.
//
// Dropping a table or column name from the sidebar inserts it at the
// cursor; see the dragover/drop handlers on the editor host.

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, crosshairCursor, drawSelection, keymap, lineNumbers, rectangularSelection, highlightActiveLine, highlightActiveLineGutter } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, insertNewline } from '@codemirror/commands'
import { bracketMatching, defaultHighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { acceptCompletion, autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { sql, PostgreSQL } from '@codemirror/lang-sql'
import { vscodeDark, vscodeLight } from '@uiw/codemirror-theme-vscode'
import { Download, Play, Save as SaveIcon, X } from 'lucide-vue-next'
import Splitter from './Splitter.vue'
import SqlResultsGrid from './SqlResultsGrid.vue'
import { isPristineSql, starterFor, useSqlStore } from '../stores/sql'
import { useTablesStore } from '../stores/tables'
import { useToasts } from '../stores/toasts'
import { useDialogs } from '../stores/dialogs'
import { useViewStore } from '../stores/view'
import { useTheme } from '../composables/useTheme'

const sqlStore = useSqlStore()
const tables = useTablesStore()
const toasts = useToasts()
const viewStore = useViewStore()
const dialogs = useDialogs()

function onProfileColumn(columnName: string) {
  if (!lastRanSql.value) return
  viewStore.profileQuery(lastRanSql.value, columnName)
}

function onPivotRowFromResults(absIndex: number) {
  if (!result.value || !result.value.ok) return
  const r = result.value
  // The grid emits the *absolute* row index; look it up through whichever
  // window / sorted buffer is currently loaded.
  const row = sqlStore.sortedRows
    ? sqlStore.sortedRows[absIndex]
    : (() => {
        const w = sqlStore.rowWindow
        if (!w) return null
        const local = absIndex - w.absStart
        return (local >= 0 && local < w.rows.length) ? w.rows[local] : null
      })()
  if (!row) return
  const keep = r.columns
    .map((c, i) => ({ c, i }))
    .filter(x => x.c.name !== '_src_row_num')
  viewStore.openPivot({
    tableLabel: '<query>',
    rowNum: absIndex + 1,
    columns: keep.map(x => ({ name: x.c.name, type: x.c.type })),
    values: keep.map(x => row[x.i]),
  })
}
const { effective: effectiveTheme } = useTheme()
const { result, running, lastRanSql } = storeToRefs(sqlStore)
const { activeName } = storeToRefs(tables)

// Editor / results split height.  The number is the editor pane's pixel
// height; the results pane fills the remainder.
const EDITOR_HEIGHT_KEY = 'fq.sqlEditorHeight'
const storedH = Number(localStorage.getItem(EDITOR_HEIGHT_KEY))
const editorHeight = ref<number>(Number.isFinite(storedH) && storedH >= 120 ? storedH : 260)
watch(editorHeight, (h) => { try { localStorage.setItem(EDITOR_HEIGHT_KEY, String(h)) } catch { /* ignore */ } })

const editorHostRef = ref<HTMLDivElement | null>(null)
let view: EditorView | null = null

function currentSqlToRun(): string {
  if (!view) return ''
  const sel = view.state.selection.main
  if (!sel.empty) {
    return view.state.doc.sliceString(sel.from, sel.to).trim()
  }
  return view.state.doc.toString().trim()
}

async function runCurrent() {
  const text = currentSqlToRun()
  if (!text) return
  await sqlStore.run(text)
  if (result.value && !result.value.ok) {
    toasts.show(result.value.error, 'error', 5000)
  } else if (result.value?.ok) {
    // Results materialize into a hidden workspace table; refresh the
    // sidebar to reflect any tables the query created (e.g. via
    // user-issued CREATE TABLE ... AS SELECT).
    void tables.refresh()
  }
}

async function cancelCurrent() {
  await sqlStore.cancel()
}

function onExportResult() {
  if (!lastRanSql.value) return
  viewStore.openExport({ kind: 'query', name: null, sql: lastRanSql.value })
}

async function onSaveAsTable() {
  if (!lastRanSql.value) return
  const name = await dialogs.prompt({
    title: 'Save query result as table',
    message: 'Pick a name for the new workspace table.',
    defaultValue: '',
    placeholder: 'e.g. orders_2025',
    okLabel: 'Save',
  })
  if (!name) return
  try {
    await sqlStore.saveAsTable(name)
    await tables.refresh()
    toasts.show(`Saved as “${name}”`, 'success')
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  }
}

// Compartment lets us reconfigure the SQL extension (specifically the
// `schema` it offers to autocomplete) as tables / columns load in,
// without rebuilding the whole editor.
const sqlCompartment = new Compartment()

/** Build the lang-sql extension with a schema dictionary
 *  (`{ tableName: [colName, ...] }`) so autocomplete can suggest
 *  workspace tables and their columns alongside SQL keywords. */
function buildSqlExtension() {
  const schema: Record<string, string[]> = {}
  for (const t of tables.tables) {
    const cols = tables.schemaByTable[t.name]
    schema[t.name] = cols ? cols.map(c => c.name) : []
  }
  return sql({ dialect: PostgreSQL, upperCaseKeywords: false, schema })
}

// Four-space soft tab.  SQL has no reliable context-aware indenter, so
// Tab inserts spaces directly instead of trying to be clever.
const TAB_SPACES = '    '

function buildExtensions(isDark: boolean) {
  // Line-wrapping is off: long SQL lines scroll horizontally instead.
  return [
    lineNumbers(),
    highlightActiveLine(),
    highlightActiveLineGutter(),
    history(),
    bracketMatching(),
    closeBrackets(),
    // Alt+drag draws a rectangular (block / column) selection.  Three
    // pieces are required:
    //   - `allowMultipleSelections` opens the state model to hold
    //     more than one selection range (it defaults to one).
    //   - `drawSelection` swaps the browser's native single-range
    //     selection painter for CodeMirror's multi-range renderer.
    //   - `rectangularSelection` is the input handler that builds
    //     the rectangle from Alt+drag.
    // `crosshairCursor` flips the cursor while Alt is held so the
    // user knows the editor is in block-select mode.
    EditorState.allowMultipleSelections.of(true),
    drawSelection(),
    rectangularSelection(),
    crosshairCursor(),
    autocompletion({ activateOnTyping: true }),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    sqlCompartment.of(buildSqlExtension()),
    keymap.of([
      // Plain newline; no auto-indent (SQL doesn't have a useful
      // context-aware strategy and copy-previous-line indent is more
      // annoying than helpful).  This must precede defaultKeymap.
      { key: 'Enter',     run: insertNewline },
      ...closeBracketsKeymap,
      ...completionKeymap,
      // Tab: accept the active completion if the popup is open;
      // otherwise insert four spaces.  Without an explicit binding Tab
      // would move focus out of the editor.
      { key: 'Tab',       run: acceptCompletion },
      { key: 'Tab',       run: (v) => {
        v.dispatch(v.state.replaceSelection(TAB_SPACES))
        return true
      } },
      ...defaultKeymap,
      ...historyKeymap,
      { key: 'F5',        run: () => { void runCurrent();   return true } },
      { key: 'Mod-Enter', run: () => { void runCurrent();   return true } },
      { key: 'Escape',    run: () => { if (running.value) void cancelCurrent(); return true } },
    ]),
    EditorView.updateListener.of(v => {
      if (v.docChanged) sqlStore.setText(v.state.doc.toString())
    }),
    // Custom drop: insert at the drop point and leave the cursor at the
    // end of the inserted text (not selected).  Returning true prevents
    // CodeMirror's default drop handler from running.
    EditorView.domEventHandlers({
      drop(event, v) {
        const text = event.dataTransfer?.getData('text/plain')
        if (!text) return false
        event.preventDefault()
        const pos = v.posAtCoords({ x: event.clientX, y: event.clientY }) ?? v.state.selection.main.head
        v.dispatch({
          changes: { from: pos, insert: text },
          selection: { anchor: pos + text.length },
        })
        v.focus()
        return true
      },
      dragover(event) {
        event.preventDefault()
        return false
      },
    }),
    isDark ? vscodeDark : vscodeLight,
  ]
}

function mountEditor() {
  if (!editorHostRef.value) return
  view?.destroy()
  view = new EditorView({
    state: EditorState.create({
      doc: sqlStore.text,
      extensions: buildExtensions(effectiveTheme.value === 'dark'),
    }),
    parent: editorHostRef.value,
  })
}

// When the SQL view opens (or when the active table changes while the
// editor is in a pristine state; empty, the default hint, or still a
// `select * from X;` starter), populate with a fresh starter query and
// auto-run it so the results area shows the table instead of blank.
watch(activeName, async (name) => {
  if (!isPristineSql(sqlStore.text)) return
  const next = starterFor(name)
  const textChanged = next !== sqlStore.text
  sqlStore.setText(next)
  await nextTick()
  if (view && view.state.doc.toString() !== next) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: next } })
  }
  if (name && (textChanged || !sqlStore.result)) {
    void sqlStore.run(next)
    void tables.refresh()
  }
}, { immediate: true })

onMounted(async () => {
  await nextTick()
  mountEditor()
  // Pull every workspace table's column list so `alias.` autocomplete
  // can resolve through the FROM clause without the user having to
  // expand each table in the sidebar first.
  void tables.ensureAllSchemas()
  window.addEventListener('click', onWindowClickCtx)
  window.addEventListener('keydown', onWindowKeyCtx)
})

onBeforeUnmount(() => {
  view?.destroy()
  view = null
  window.removeEventListener('click', onWindowClickCtx)
  window.removeEventListener('keydown', onWindowKeyCtx)
})

watch(effectiveTheme, () => {
  const current = view?.state.doc.toString() ?? sqlStore.text
  sqlStore.setText(current)
  mountEditor()
})

// Re-feed lang-sql's `schema` only when the table list or the set of
// tables with cached column lists changes.  A coarser deep watch fires
// on unrelated tables-store changes (row-window updates, sort flips,
// etc.) and the resulting reconfigure churn closes any open
// autocomplete popup mid-keystroke.
const sqlSchemaFingerprint = computed(() => {
  const names = tables.tables.map(t => t.name).sort().join('|')
  const expanded = Object.keys(tables.schemaByTable).sort().join('|')
  return `${names}::${expanded}`
})
watch(sqlSchemaFingerprint, () => {
  if (!view) return
  view.dispatch({ effects: sqlCompartment.reconfigure(buildSqlExtension()) })
})

// Pull schemas for any newly-added tables so their column lists land
// in `schemaByTable` and the next fingerprint tick rebuilds the
// completion extension with full column data.
watch(() => tables.tables.map(t => t.name).join('|'), () => {
  void tables.ensureAllSchemas()
})

// CodeMirror 6 handles `text/plain` drops natively and inserts at the
// mouse position, which is what we want.  A custom handler here would
// fire in addition to CodeMirror's and double-insert the text.

// ---- editor right-click menu ----------------------------------------- //
// Cut / Copy / Paste / Run.  CodeMirror has no built-in context menu and
// the OS one isn't available inside the webview, so we render our own.
const ctxMenu = ref<{ x: number; y: number } | null>(null)
const ctxHasSelection = ref(false)

function onEditorContextMenu(e: MouseEvent) {
  if (!view) return
  e.preventDefault()
  const sel = view.state.selection.main
  ctxHasSelection.value = !sel.empty
  ctxMenu.value = { x: e.clientX, y: e.clientY }
}

function closeCtxMenu() { ctxMenu.value = null }

async function onCut() {
  if (!view) { closeCtxMenu(); return }
  const sel = view.state.selection.main
  if (sel.empty) { closeCtxMenu(); return }
  const text = view.state.doc.sliceString(sel.from, sel.to)
  try { await navigator.clipboard.writeText(text) } catch (e) { console.warn('[SqlEditor] cut failed:', e) }
  view.dispatch({
    changes: { from: sel.from, to: sel.to, insert: '' },
    selection: { anchor: sel.from },
  })
  view.focus()
  closeCtxMenu()
}

async function onCopy() {
  if (!view) { closeCtxMenu(); return }
  const sel = view.state.selection.main
  if (sel.empty) { closeCtxMenu(); return }
  const text = view.state.doc.sliceString(sel.from, sel.to)
  try { await navigator.clipboard.writeText(text) } catch (e) { console.warn('[SqlEditor] copy failed:', e) }
  view.focus()
  closeCtxMenu()
}

async function onPaste() {
  if (!view) { closeCtxMenu(); return }
  // Prefer the Python-side clipboard reader;`navigator.clipboard.readText`
  // raises a WebView2 permission prompt and even after Allow it tends
  // to come back empty.  Fall back to the browser API only if the
  // pywebview bridge isn't reachable (e.g. running in a plain browser
  // during dev with `npm run dev`).
  let text = ''
  try {
    if (window.pywebview?.api?.clipboard_read) {
      text = (await window.pywebview.api.clipboard_read()) || ''
    } else {
      text = await navigator.clipboard.readText()
    }
  } catch (e) {
    console.warn('[SqlEditor] paste failed:', e)
  }
  if (text) {
    const sel = view.state.selection.main
    view.dispatch({
      changes: { from: sel.from, to: sel.to, insert: text },
      selection: { anchor: sel.from + text.length },
    })
  }
  view.focus()
  closeCtxMenu()
}

function onRunFromMenu() {
  closeCtxMenu()
  void runCurrent()
}

function onWindowClickCtx() { if (ctxMenu.value) closeCtxMenu() }
function onWindowKeyCtx(e: KeyboardEvent) { if (e.key === 'Escape' && ctxMenu.value) closeCtxMenu() }

const resultOk = computed(() => result.value && result.value.ok)
const errorMsg = computed(() => (result.value && !result.value.ok) ? result.value.error : null)

// Show metadata columns (_src_row_num) when the last-run SQL text
// mentions them by name; otherwise hide.  Simple text match is enough;
// the name is distinctive and unlikely to appear in unrelated SQL.
const sqlMentionsMeta = computed(() =>
  (lastRanSql.value ?? '').toLowerCase().includes('_src_row_num'),
)
</script>

<template>
  <section class="flex min-h-0 min-w-0 flex-1 flex-col bg-surface-0">
    <!-- Editor toolbar -->
    <div class="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-surface-0 px-3 text-xs text-ink-muted">
      <button
        type="button"
        class="btn-run"
        :disabled="running"
        @click="runCurrent"
      >
        <Play class="h-3 w-3" />
        Run
      </button>
      <button
        type="button"
        class="btn-secondary"
        :disabled="!running"
        @click="cancelCurrent"
      >
        <X class="h-3 w-3" />
        Cancel
      </button>

      <div class="mx-1 h-5 w-px bg-border" />

      <button
        type="button"
        class="btn-secondary"
        :disabled="!resultOk || running"
        @click="onSaveAsTable"
      >
        <SaveIcon class="h-3 w-3" />
        Save as table
      </button>

      <button
        type="button"
        class="btn-secondary"
        :disabled="!resultOk || running"
        @click="onExportResult"
      >
        <Download class="h-3 w-3" />
        Export
      </button>

      <span class="ml-auto whitespace-nowrap font-mono tabular-nums">
        <template v-if="running">running…</template>
        <template v-else-if="resultOk && result && result.ok">
          {{ result.row_count.toLocaleString() }} rows
          <span class="mx-1.5 text-ink-subtle">·</span>
          {{ result.elapsed_ms.toLocaleString() }} ms
        </template>
      </span>
    </div>

    <!-- Editor pane; fixed height, resizable via splitter below -->
    <div
      class="shrink-0 overflow-hidden"
      :style="{ height: editorHeight + 'px' }"
    >
      <div
        ref="editorHostRef"
        class="h-full w-full cm-host"
        @contextmenu="onEditorContextMenu"
      />
    </div>

    <!-- Right-click menu inside the SQL editor -->
    <div
      v-if="ctxMenu"
      class="fq-ctx-menu"
      :style="{ top: `${ctxMenu.y}px`, left: `${ctxMenu.x}px` }"
      @click.stop
      @contextmenu.prevent
    >
      <button
        class="fq-ctx-item"
        :disabled="!ctxHasSelection"
        @click="onCut"
      >
        Cut
      </button>
      <button
        class="fq-ctx-item"
        :disabled="!ctxHasSelection"
        @click="onCopy"
      >
        Copy
      </button>
      <button class="fq-ctx-item" @click="onPaste">
        Paste
      </button>
      <div class="fq-ctx-sep" />
      <button class="fq-ctx-item" :disabled="running" @click="onRunFromMenu">
        {{ ctxHasSelection ? 'Run Selection' : 'Run Query' }}
      </button>
    </div>

    <Splitter
      v-model:size="editorHeight"
      orientation="horizontal"
      :min="140"
      :max="800"
    />

    <!-- Error banner -->
    <div
      v-if="errorMsg"
      class="shrink-0 border-b border-danger/40 bg-danger/5 px-3 py-2 font-mono text-xs text-danger overflow-auto max-h-[30%]"
    >
      <pre class="whitespace-pre-wrap">{{ errorMsg }}</pre>
    </div>

    <!-- Results -->
    <SqlResultsGrid
      v-if="resultOk && result && result.ok"
      :columns="result.columns"
      :show-meta-cols="sqlMentionsMeta"
      @profile-column="onProfileColumn"
      @pivot-row="onPivotRowFromResults"
    />
    <div v-else class="flex flex-1 items-center justify-center text-xs text-ink-subtle">
      <template v-if="!result">Run a query to see results.</template>
    </div>
  </section>
</template>

<style scoped>
.cm-host :deep(.cm-editor) {
  height: 100%;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 13px;
}
.cm-host :deep(.cm-scroller) {
  font-family: inherit;
}

</style>
