<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import {
  ChevronDown,
  ChevronRight,
  Columns3,
  Database,
  DatabaseZap,
  Eye,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Layers,
  MoreHorizontal,
  Table2,
} from 'lucide-vue-next'
import { autoDerive, openFileDialog } from '../lib/api'
import { useTablesStore } from '../stores/tables'
import { useToasts } from '../stores/toasts'
import { useViewStore } from '../stores/view'
import { useDialogs } from '../stores/dialogs'
import DeriveDialog from './DeriveDialog.vue'

const store = useTablesStore()
const view = useViewStore()
const toasts = useToasts()
const dialogs = useDialogs()
const { tables, activeName, schemaByTable, expandedTables, ingestInfo } = storeToRefs(store)

const deriveSource = ref<string | null>(null)
const menuOpenFor = ref<string | null>(null)
const openMenuOpen = ref(false)

onMounted(() => { void store.refresh(); document.addEventListener('click', onDocClick) })
onBeforeUnmount(() => { document.removeEventListener('click', onDocClick) })

function onDocClick(ev: MouseEvent) {
  const t = ev.target as HTMLElement | null
  if (!(t && t.closest('[data-open-menu]'))) openMenuOpen.value = false
  if (t && t.closest('[data-table-menu]')) return
  menuOpenFor.value = null
}

function toggleMenu(name: string) {
  menuOpenFor.value = menuOpenFor.value === name ? null : name
}

function fmtCount(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}

async function onOpen() {
  openMenuOpen.value = false
  try { await store.openFile() } catch (e) { console.warn('[Sidebar] openFile:', e) }
}

async function onOpenWithOptions() {
  openMenuOpen.value = false
  try {
    const path = await openFileDialog()
    if (!path) return
    // openPath routes by format: real Excel/Parquet get their own
    // chooser dialogs; text lands in IngestOptionsDialog.
    await store.openPath(path, { advanced: true })
  } catch (e) {
    console.warn('[Sidebar] openWithOptions:', e)
  }
}

async function onReloadWithOptions(name: string) {
  menuOpenFor.value = null
  const info = ingestInfo.value[name]
  if (!info?.source_path) {
    toasts.show('No source path on file — open the file fresh instead.', 'warn', 3500)
    return
  }
  // Excel sheets get their own table name per sheet, so reusing one
  // name on reload only makes sense for CSV.
  if (info.format === 'csv') {
    view.openIngestOptions({ path: info.source_path, reloadTable: name })
  } else {
    await store.openPath(info.source_path, { advanced: true })
  }
}

function onSelectTable(name: string) {
  view.setMode('grid')
  void store.selectTable(name)
}

async function onRename(name: string) {
  menuOpenFor.value = null
  const next = await dialogs.prompt({
    title: 'Rename table',
    message: `Currently named “${name}”.`,
    defaultValue: name,
    okLabel: 'Rename',
  })
  if (!next || next === name) return
  if (store.activeName !== name) await store.selectTable(name)
  await store.renameActive(next)
}

async function onDrop(name: string) {
  menuOpenFor.value = null
  const ok = await dialogs.confirm({
    title: `Drop “${name}”?`,
    message: 'This removes it from the workspace.',
    okLabel: 'Drop',
    danger: true,
  })
  if (!ok) return
  await store.dropByName(name)
}

async function onMaterialize(name: string) {
  menuOpenFor.value = null
  if (store.activeName !== name) await store.selectTable(name)
  await store.materializeActive()
}

function onDerive(name: string) {
  menuOpenFor.value = null
  deriveSource.value = name
}

function onExport(name: string) {
  menuOpenFor.value = null
  view.openExport({ kind: 'table', name, sql: null })
}

async function onAutoType(name: string) {
  menuOpenFor.value = null
  const newName = await dialogs.prompt({
    title: 'Clone with Auto-type',
    message: 'Pick a name for the new typed table.',
    defaultValue: `${name}_typed`,
    okLabel: 'Clone',
  })
  if (!newName) return
  const toastId = toasts.showSticky(`Cloning ${name} with auto-type…`, 'info')
  try {
    const r = await autoDerive(name, newName)
    toasts.dismiss(toastId)
    if (r.ok) {
      await store.refresh()
      toasts.show(`Created “${r.name}” — ${r.row_count.toLocaleString()} rows`, 'success')
    }
  } catch (e) {
    toasts.dismiss(toastId)
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  }
}

function onShowDdl(name: string) {
  menuOpenFor.value = null
  view.openDdl(name)
}

// SQL identifier quoting (see DeriveDialog / SqlEditor drop handling).
function sqlIdent(name: string): string {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : '"' + name.replace(/"/g, '""') + '"'
}

function onDragTable(ev: DragEvent, name: string) {
  ev.dataTransfer?.setData('text/plain', sqlIdent(name))
  if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'copy'
}

function onDragColumn(ev: DragEvent, _tableName: string, colName: string) {
  ev.dataTransfer?.setData('text/plain', sqlIdent(colName))
  if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'copy'
}

function onDragAllColumns(ev: DragEvent, tableName: string) {
  const cols = schemaByTable.value[tableName]
  if (!cols || !cols.length) return
  // `_src_row_num` is metadata and excluded from the all-columns bundle;
  // the user can still drag it in individually if they want it.
  const dataCols = cols.filter(c => c.name !== '_src_row_num')
  if (!dataCols.length) return
  const text = dataCols
    .map((c, i) => (i === 0 ? '' : '\n     , ') + sqlIdent(c.name))
    .join('')
  ev.dataTransfer?.setData('text/plain', text)
  if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'copy'
}

function titleFor(name: string): string {
  const info = ingestInfo.value[name]
  return info?.source_path ? `${name}\n${info.source_path}` : name
}

/** Icon for a table in the sidebar — distinctive per source format.
 *  Tables without ingest info (derived / auto-typed / saved-from-query)
 *  fall back to the generic table / view icon. */
function iconFor(name: string, kind: 'table' | 'view') {
  const fmt = ingestInfo.value[name]?.format
  if (fmt === 'csv')     return FileText
  if (fmt === 'xlsx')    return FileSpreadsheet
  if (fmt === 'parquet') return Layers
  return kind === 'view' ? Eye : Table2
}
</script>

<template>
  <aside
    class="flex shrink-0 flex-col bg-surface-0"
    aria-label="Tables"
  >
    <div class="flex h-8 shrink-0 items-center gap-1.5 px-2 pl-3 text-2xs font-medium uppercase tracking-wide text-ink-subtle">
      <Database class="h-3 w-3" />
      <span>Tables</span>
      <span v-if="tables.length" class="font-mono tabular-nums text-ink-subtle/70">
        {{ tables.length }}
      </span>
      <button
        type="button"
        class="btn-icon fq-yellow-fg ml-auto"
        title="Import from database…"
        aria-label="Import from database"
        @click="view.openImportDb()"
      >
        <DatabaseZap class="h-3.5 w-3.5" />
      </button>
      <div class="relative flex items-center" data-open-menu>
        <button
          type="button"
          class="btn-icon fq-yellow-fg"
          title="Open file"
          aria-label="Open file"
          @click="onOpen"
        >
          <FolderOpen class="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          class="-ml-1 inline-flex h-7 w-4 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-2 hover:text-ink"
          title="More open options"
          aria-label="More open options"
          @click="openMenuOpen = !openMenuOpen"
        >
          <ChevronDown class="h-3 w-3" />
        </button>
        <div
          v-if="openMenuOpen"
          class="absolute right-0 top-8 z-30 min-w-[200px] rounded-md border border-border bg-surface-1 py-1 text-xs shadow-lg"
        >
          <button type="button" class="ctx-item" @click="onOpen">Open…</button>
          <button type="button" class="ctx-item" @click="onOpenWithOptions">Open with options…</button>
        </div>
      </div>
    </div>

    <div class="flex-1 overflow-y-auto px-1 pb-2">
      <div
        v-if="!tables.length"
        class="mx-2 mt-6 flex flex-col items-center gap-3 rounded-md border border-dashed border-border px-3 py-6 text-center"
      >
        <p class="text-xs text-ink-muted">No tables loaded</p>
        <p class="max-w-[200px] text-2xs text-ink-subtle">
          Open a CSV, Parquet, or Excel file to get started.
        </p>
        <button type="button" class="btn-yellow mt-1" @click="onOpen">
          <FolderOpen class="h-3.5 w-3.5" />
          Open a file
        </button>
      </div>

      <ul v-else class="space-y-0.5" role="list">
        <li v-for="t in tables" :key="t.name">
          <div
            class="group relative flex h-7 items-center gap-1 rounded-md pl-1 pr-1 text-sm cursor-pointer"
            :class="[
              t.name === activeName
                ? 'bg-surface-2 text-ink'
                : 'text-ink-secondary hover:bg-surface-2/60 hover:text-ink',
            ]"
            @click="onSelectTable(t.name)"
          >
            <button
              type="button"
              class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-subtle hover:text-ink"
              :title="expandedTables[t.name] ? 'Collapse' : 'Expand columns'"
              :aria-label="expandedTables[t.name] ? 'Collapse columns' : 'Expand columns'"
              @click.stop="store.toggleExpand(t.name)"
            >
              <component
                :is="expandedTables[t.name] ? ChevronDown : ChevronRight"
                class="h-3 w-3"
              />
            </button>
            <component
              :is="iconFor(t.name, t.kind)"
              class="h-3.5 w-3.5 shrink-0"
              :class="t.kind === 'view' ? 'text-ink-subtle' : 'text-ink-muted'"
              :aria-label="t.kind"
            />
            <span
              class="truncate select-none"
              :title="titleFor(t.name)"
              draggable="true"
              @dragstart.stop="onDragTable($event, t.name)"
            >{{ t.name }}</span>
            <span class="ml-auto shrink-0 font-mono text-2xs tabular-nums text-ink-subtle">
              {{ fmtCount(t.row_count) }}
            </span>

            <!-- Context menu (controlled dropdown; closes on outside click) -->
            <div class="relative" data-table-menu @click.stop>
              <button
                type="button"
                class="btn-icon h-5 w-5 opacity-50 transition-opacity group-hover:opacity-100"
                :aria-label="`Actions for ${t.name}`"
                title="Actions"
                @click="toggleMenu(t.name)"
              >
                <MoreHorizontal class="h-3.5 w-3.5" />
              </button>
              <div
                v-if="menuOpenFor === t.name"
                class="absolute right-0 top-6 z-20 min-w-[180px] rounded-md border border-border bg-surface-1 py-1 text-xs shadow-lg"
              >
                <button type="button" class="ctx-item" @click="onRename(t.name)">Rename…</button>
                <button
                  v-if="ingestInfo[t.name]?.source_path"
                  type="button"
                  class="ctx-item"
                  @click="onReloadWithOptions(t.name)"
                >
                  Reload with options…
                </button>
                <button type="button" class="ctx-item" @click="onDerive(t.name)">Clone…</button>
                <button type="button" class="ctx-item" @click="onAutoType(t.name)">Clone with Auto-type</button>
                <button type="button" class="ctx-item" @click="onShowDdl(t.name)">Show DDL…</button>
                <button type="button" class="ctx-item" @click="onExport(t.name)">Export…</button>
                <button
                  type="button"
                  class="ctx-item"
                  :disabled="t.kind === 'table'"
                  @click="onMaterialize(t.name)"
                >
                  Materialize
                </button>
                <div class="my-1 h-px bg-border" />
                <button type="button" class="ctx-item text-danger" @click="onDrop(t.name)">
                  Drop
                </button>
              </div>
            </div>
          </div>

          <ul
            v-if="expandedTables[t.name] && schemaByTable[t.name]"
            class="ml-7 mt-0.5 mb-1 space-y-px border-l border-border/60 pl-2"
            role="list"
          >
            <li
              v-if="schemaByTable[t.name].filter(c => c.name !== '_src_row_num').length > 1"
              class="flex h-6 items-center gap-1.5 rounded-md px-1 text-2xs text-ink-subtle cursor-grab"
              :title="`Drag to insert all ${schemaByTable[t.name].filter(c => c.name !== '_src_row_num').length} columns qualified by table name`"
              draggable="true"
              @dragstart.stop="onDragAllColumns($event, t.name)"
            >
              <Columns3 class="h-3 w-3" />
              <span class="italic">all columns</span>
            </li>
            <li
              v-for="col in schemaByTable[t.name]"
              :key="col.name"
              class="flex h-6 items-center gap-1.5 rounded-md px-1 text-xs hover:bg-surface-2/60 cursor-grab"
              :class="col.name === '_src_row_num' ? 'italic text-ink-subtle' : 'text-ink-secondary'"
              :title="col.name === '_src_row_num'
                ? '_src_row_num · metadata — original position in the source file (hidden from the grid; drag to insert)'
                : `${col.name} · ${col.type} — drag to insert`"
              draggable="true"
              @dragstart.stop="onDragColumn($event, t.name, col.name)"
            >
              <span class="truncate">{{ col.name }}</span>
              <span
                v-if="col.name === '_src_row_num'"
                class="ml-auto shrink-0 rounded border border-border px-1 font-mono text-[9px] uppercase not-italic tracking-wide text-ink-subtle"
              >meta</span>
              <span
                v-else
                class="ml-auto shrink-0 font-mono text-[10px] text-ink-subtle"
              >{{ col.type }}</span>
            </li>
          </ul>
        </li>
      </ul>
    </div>

    <DeriveDialog
      v-if="deriveSource"
      :source="deriveSource"
      @close="deriveSource = null"
    />
  </aside>
</template>

<style scoped>
.ctx-item {
  @apply flex w-full items-center px-3 py-1.5 text-left text-ink hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40;
}
</style>
