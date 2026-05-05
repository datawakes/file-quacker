<script setup lang="ts">
// Export a workspace table to SQL Server.
//
// Mirrors DeriveDialog's modal pattern.  Two-column body: connection +
// target on top (schema dropdown populates on successful Test); column
// mapping + DDL preview on bottom.  Footer carries a phase strip that
// lights up as we move through connect → create → insert → verify.

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Copy, ExternalLink, Play, Upload, X } from 'lucide-vue-next'
import type { ExportSource } from '../lib/api'
import {
  cancelExport,
  checkOdbcDriver,
  exportToSqlServer,
  exportToFile,
  getExportProgress,
  previewExportDdl,
  suggestExportMappings,
  type ExportColumnMapping,
  type ExportErrorRow,
  type ExportProgress,
  type ExportResult,
  type FileTarget,
  type SqlServerConnectionOptions,
  type SqlServerExportOptions,
} from '../lib/api'
import { useToasts } from '../stores/toasts'
import { useFocusTrap } from '../composables/useFocusTrap'
import Splitter from './Splitter.vue'
import SqlServerTargetPane from './export/SqlServerTargetPane.vue'
import FileTargetPane from './export/FileTargetPane.vue'

const dialogRef = ref<HTMLElement | null>(null)
useFocusTrap(dialogRef)

const props = defineProps<{ source: ExportSource }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'exported', name: string): void
}>()

const sourceLabel = computed(() =>
  props.source.kind === 'table' ? (props.source.name ?? '') : '<query>'
)

const toasts = useToasts()

// --- target selector (anchors the dropdown added in Task 8) ---------- //
const target = ref<'sqlserver' | 'flat' | 'parquet' | 'excel'>('sqlserver')

const fileTarget = ref<FileTarget>({
  kind: 'flat', path: '', delimiter: '|', header: true, quote: '"', null: '', line_ending: 'crlf',
})

// When the target dropdown changes, swap fileTarget's default shape.
watch(target, (newT) => {
  if (newT === 'flat')    fileTarget.value = { kind: 'flat', path: '', delimiter: '|', header: true, quote: '"', null: '', line_ending: 'crlf' }
  if (newT === 'parquet') fileTarget.value = { kind: 'parquet', path: '', compression: 'snappy' }
  if (newT === 'excel')   fileTarget.value = { kind: 'excel', path: '', sheet_name: 'Sheet1' }
})

// --- connection + target state --------------------------------------- //
const conn = ref<SqlServerConnectionOptions>({
  server: '',
  database: '',
  auth: 'sql',
  username: '',
  password: '',
  port: null,
  azure: false,
})
const schema = ref('dbo')
const tableName = ref(props.source.kind === 'table' ? (props.source.name ?? '') : 'query_export')
const ifExists = ref<'fail' | 'drop' | 'append'>('fail')

// --- mapping rows ---------------------------------------------------- //
interface Row extends ExportColumnMapping { source_type: string }
const rows = ref<Row[]>([])

// --- preview + status ------------------------------------------------ //
const ddl = ref('')
const driverCheck = ref<{ ok: boolean; drivers: string[]; required: string } | null>(null)
const progress = ref<ExportProgress | null>(null)
const lastResult = ref<ExportResult | null>(null)
const exporting = ref(false)
const loading = ref(true)

// --- splitter width (bottom row: mapping | ddl preview) ------------- //
const mappingWidth = ref(640)

// --- lifecycle ------------------------------------------------------- //
let pollTimer: number | null = null
let ddlDebounce: number | null = null
// Guard so async work in flight at unmount-time can't write to refs
// after Vue has torn the component down (e.g. dialog closed mid-export).
let mounted = true

onMounted(async () => {
  try {
    const dc = await checkOdbcDriver()
    if (!mounted) return
    driverCheck.value = dc
    const suggestions = await suggestExportMappings(props.source)
    if (!mounted) return
    rows.value = suggestions.map(s => ({
      source: s.source,
      source_type: s.source_type,
      target: s.target,
      sql_type: s.sql_type,
      nullable: s.nullable,
    }))
    scheduleDdlRefresh()
  } catch (e) {
    if (mounted) toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  } finally {
    if (mounted) loading.value = false
  }
})

onBeforeUnmount(() => {
  mounted = false
  if (pollTimer != null) window.clearInterval(pollTimer)
  if (ddlDebounce != null) window.clearTimeout(ddlDebounce)
})

// --- DDL preview (debounced) ----------------------------------------- //
watch([rows, schema, tableName], scheduleDdlRefresh, { deep: true })

function scheduleDdlRefresh() {
  if (ddlDebounce != null) window.clearTimeout(ddlDebounce)
  ddlDebounce = window.setTimeout(refreshDdl, 250)
}

async function refreshDdl() {
  if (!tableName.value || rows.value.length === 0) { ddl.value = ''; return }
  try {
    const r = await previewExportDdl(buildExportOpts())
    ddl.value = r.sql
  } catch (e) {
    ddl.value = `-- preview error: ${e instanceof Error ? e.message : String(e)}`
  }
}

// --- export ---------------------------------------------------------- //
function buildExportOpts(): SqlServerExportOptions {
  return {
    source: props.source,
    schema: schema.value,
    table_name: tableName.value,
    mappings: rows.value.map(r => ({
      source: r.source, target: r.target,
      sql_type: r.sql_type, nullable: r.nullable,
      source_type: r.source_type,
    })),
    if_exists: ifExists.value,
  }
}

function startPolling() {
  if (pollTimer != null) window.clearInterval(pollTimer)
  pollTimer = window.setInterval(async () => {
    try { progress.value = await getExportProgress() } catch { /* ignore */ }
  }, 250)
}

function stopPolling() {
  if (pollTimer != null) { window.clearInterval(pollTimer); pollTimer = null }
}

async function onExport() {
  if (exporting.value) return
  if (target.value === 'sqlserver') {
    return onExportSqlServer()
  }
  return onExportFile()
}

async function onExportSqlServer() {
  if (!canExport.value) return
  exporting.value = true
  lastResult.value = null
  progress.value = { phase: 'connecting', rows_written: 0, total_rows: 0,
    chunk_no: 0, chunk_rows: 0, last_chunk_ms: 0, elapsed_ms: 0, error: null }
  startPolling()
  try {
    const r = await exportToSqlServer(conn.value, buildExportOpts())
    lastResult.value = r
    progress.value = await getExportProgress()
    if (r.ok) {
      toasts.show(
        `Exported ${r.rows_written?.toLocaleString() ?? '?'} rows to ${schema.value}.${tableName.value}`,
        'success', 5000,
      )
      emit('exported', `${schema.value}.${tableName.value}`)
    }
  } catch (e) {
    lastResult.value = { ok: false, error: e instanceof Error ? e.message : String(e), elapsed_ms: 0 }
  } finally {
    stopPolling()
    exporting.value = false
  }
}

async function onExportFile() {
  exporting.value = true
  lastResult.value = null
  progress.value = { phase: 'writing', rows_written: 0, total_rows: 0,
    chunk_no: 0, chunk_rows: 0, last_chunk_ms: 0, elapsed_ms: 0, error: null }
  try {
    const fileMappings = rows.value.map(r => ({
      source: r.source, target: r.target,
      sql_type: r.sql_type, nullable: r.nullable,
      source_type: r.source_type,
    }))
    const r = await exportToFile(props.source, fileTarget.value, fileMappings)
    lastResult.value = r.ok
      ? { ok: true, rows_written: r.rows_written, elapsed_ms: r.elapsed_ms }
      : { ok: false, error: r.error, elapsed_ms: r.elapsed_ms }
    progress.value = {
      phase: r.ok ? 'done' : 'error',
      rows_written: r.rows_written ?? 0,
      total_rows: r.rows_written ?? 0,
      chunk_no: 0, chunk_rows: 0, last_chunk_ms: 0,
      elapsed_ms: r.elapsed_ms,
      error: r.ok ? null : (r.error ?? null),
    }
    if (r.ok) {
      toasts.show(
        `Wrote ${r.rows_written?.toLocaleString() ?? '?'} rows → ${r.path}`,
        'success', 5000,
      )
      emit('exported', r.path ?? '')
    } else {
      toasts.show(r.error ?? 'Export failed', 'error', 6000)
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    lastResult.value = { ok: false, error: msg, elapsed_ms: 0 }
    progress.value = { phase: 'error', rows_written: 0, total_rows: 0,
      chunk_no: 0, chunk_rows: 0, last_chunk_ms: 0, elapsed_ms: 0, error: msg }
    toasts.show(msg, 'error', 6000)
  } finally {
    exporting.value = false
  }
}

async function onCancelExport() {
  try {
    await cancelExport()
    toasts.show('Cancel requested — rolling back…', 'warn', 3000)
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  }
}

// --- computed ------------------------------------------------------- //
const driverMissing = computed(() => driverCheck.value !== null && !driverCheck.value.ok)
const connectionFilled = computed(() => {
  if (!conn.value.server) return false
  if (conn.value.auth !== 'windows' && !(conn.value.username && conn.value.password)) return false
  return !!conn.value.database
})
const canExport = computed(() => {
  if (exporting.value || loading.value) return false
  if (target.value === 'sqlserver') {
    return !driverMissing.value && connectionFilled.value && !!schema.value && !!tableName.value && rows.value.length > 0
  }
  return !!fileTarget.value.path
})
const phaseSteps = computed<ExportProgress['phase'][]>(() =>
  target.value === 'sqlserver'
    ? ['connecting', 'creating', 'inserting', 'verify']
    : ['writing']
)
const phaseLabel: Record<ExportProgress['phase'], string> = {
  idle: '', connecting: 'connect', creating: 'create',
  inserting: 'insert', verify: 'verify', writing: 'write',
  done: 'done', error: 'error',
}

function phaseState(step: ExportProgress['phase']): 'pending' | 'active' | 'done' | 'error' {
  const p = progress.value
  const steps = phaseSteps.value
  if (!p || p.phase === 'idle') return 'pending'
  if (p.phase === 'error') {
    const lastBeforeError = errorAtPhase()
    if (step === lastBeforeError) return 'error'
    const idx = steps.indexOf(step)
    const errIdx = steps.indexOf(lastBeforeError)
    return idx < errIdx ? 'done' : 'pending'
  }
  if (p.phase === 'done') return 'done'
  const idx = steps.indexOf(step)
  const cur = steps.indexOf(p.phase as ExportProgress['phase'])
  if (idx < cur) return 'done'
  if (idx === cur) return 'active'
  return 'pending'
}

function errorAtPhase(): ExportProgress['phase'] {
  // Best-effort: for SQL Server, map rows/total counters back to the
  // phase we were in when we died.  For file targets there's only one
  // phase anyway ("writing").
  const p = progress.value
  if (target.value !== 'sqlserver') return 'writing'
  if (!p) return 'connecting'
  if (p.rows_written > 0) return 'inserting'
  if (p.total_rows > 0) return 'creating'
  return 'connecting'
}

const errorRows = computed<ExportErrorRow[]>(() => lastResult.value?.error_rows ?? [])

async function copyError() {
  if (!lastResult.value || lastResult.value.ok) return
  const parts: string[] = [lastResult.value.error ?? '']
  if (errorRows.value.length) {
    parts.push('')
    for (const er of errorRows.value) {
      parts.push(`row ${er.row.toLocaleString()}: ${er.message}`)
      if (er.marked.length) parts.push(`  columns: ${er.marked.join(', ')}`)
    }
  }
  try {
    await navigator.clipboard.writeText(parts.join('\n'))
    toasts.show('Error copied to clipboard', 'success', 2000)
  } catch {
    toasts.show('Copy failed', 'error')
  }
}

const fileOperationPreview = computed(() => {
  const s = props.source.kind === 'table'
    ? `"${props.source.name}"`
    : `(${props.source.sql?.trim().replace(/;\s*$/, '')})`
  const t = fileTarget.value
  const sel = rows.value.length
    ? rows.value.map(r => `"${r.source}" as "${r.target}"`).join(', ')
    : '*'
  if (t.kind === 'flat') {
    const delimDisplay = t.delimiter === '\t' ? '\\t' : t.delimiter
    return `copy (select ${sel} from ${s})\nto '${t.path}'\n(header ${t.header}, delimiter '${delimDisplay}', quote '${t.quote}', null '${t.null}')\n;`
  }
  if (t.kind === 'parquet') {
    return `copy (select ${sel} from ${s})\nto '${t.path}'\n(format parquet, compression ${t.compression})\n;`
  }
  if (t.kind === 'excel') {
    return `-- Excel via openpyxl: sheet "${t.sheet_name}"\n-- path: ${t.path}\n-- columns: ${rows.value.map(r => r.target).join(', ') || '(all)'}`
  }
  return ''
})
</script>

<template>
  <div class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-[1px]">
    <div
      ref="dialogRef"
      class="flex flex-col rounded-md border border-border bg-surface-1 shadow-xl"
      style="height: min(820px, 92vh); width: min(1140px, 96vw);"
      role="dialog"
      aria-modal="true"
      aria-labelledby="export-title"
    >
      <!-- Header -->
      <div class="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <h2 id="export-title" class="text-sm font-medium text-ink">
          Export
          <span class="ml-1 font-mono text-ink-muted">from {{ sourceLabel }}</span>
        </h2>
        <div class="mx-1 h-5 w-px bg-border" />
        <label class="flex items-center gap-1.5 text-xs text-ink-muted">
          <span>to</span>
          <select
            v-model="target"
            class="h-6 rounded border border-border bg-surface-0 px-2 font-mono text-xs text-ink outline-none focus:border-accent"
          >
            <option value="sqlserver">SQL Server</option>
            <option value="flat">Flat file</option>
            <option value="parquet">Parquet</option>
            <option value="excel">Excel</option>
          </select>
        </label>
        <span v-if="!loading && rows.length" class="text-2xs text-ink-subtle">
          {{ rows.length }} columns
        </span>
        <button type="button" class="btn-icon ml-auto" aria-label="Close" title="Close" @click="$emit('close')">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- Driver-missing banner -->
      <div
        v-if="driverMissing && target === 'sqlserver'"
        class="shrink-0 border-b border-border bg-[rgb(var(--danger))]/10 px-3 py-2 text-xs text-[rgb(var(--danger))]"
      >
        <div class="font-medium">Missing ODBC driver: {{ driverCheck?.required }}</div>
        <div class="mt-0.5 text-ink-muted">
          Install it from Microsoft, then reopen this dialog.
          <a
            class="inline-flex items-center gap-1 text-[rgb(var(--danger))] underline"
            href="https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server"
            target="_blank"
            rel="noopener"
          >
            download page
            <ExternalLink class="h-3 w-3" />
          </a>
        </div>
      </div>

      <!-- Body: unified top form + resizable bottom split -->
      <div class="flex min-h-0 flex-1 flex-col">
        <!-- Top: connection + target pane (SQL Server) -->
        <SqlServerTargetPane
          v-if="target === 'sqlserver'"
          v-model:conn="conn"
          v-model:schema="schema"
          v-model:table="tableName"
          v-model:ifExists="ifExists"
          :driver-missing="driverMissing"
        />
        <FileTargetPane
          v-else
          v-model:target="fileTarget"
          :driver-missing="driverMissing"
        />

        <!-- Bottom: column mapping | splitter | preview (all targets) -->
        <div class="flex min-h-0 flex-1">
          <!-- Column mapping.  Type + Null columns only matter for SQL Server. -->
          <section
            class="flex min-h-0 shrink-0 flex-col"
            :style="{ width: mappingWidth + 'px' }"
          >
            <div class="flex shrink-0 items-center justify-between px-3 pb-1 pt-3">
              <h3 class="text-2xs uppercase tracking-wide text-ink-subtle">Column mapping</h3>
              <span class="text-2xs text-ink-subtle">{{ rows.length }} cols</span>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-3 pb-3 text-xs">
              <div v-if="loading" class="text-ink-subtle">Inspecting columns…</div>
              <table v-else class="w-full border-collapse">
                <thead>
                  <tr class="sticky top-0 bg-surface-1 text-left text-2xs uppercase tracking-wide text-ink-subtle">
                    <th class="py-1 pr-2 font-medium">Source</th>
                    <th class="py-1 pr-2 font-medium">Target</th>
                    <template v-if="target === 'sqlserver'">
                      <th class="py-1 pr-2 font-medium">Type</th>
                      <th class="py-1 pr-2 text-center font-medium">Null</th>
                    </template>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(r, i) in rows" :key="r.source" class="border-t border-border/50 align-middle">
                    <td class="py-1 pr-2">
                      <div class="truncate font-mono text-ink" :title="`${r.source} · ${r.source_type}`">
                        {{ r.source }}
                      </div>
                    </td>
                    <td class="py-1 pr-2">
                      <input v-model="rows[i].target" type="text"
                        class="h-6 w-full rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent" />
                    </td>
                    <template v-if="target === 'sqlserver'">
                      <td class="py-1 pr-2">
                        <input v-model="rows[i].sql_type" type="text"
                          class="h-6 w-full rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent" />
                      </td>
                      <td class="py-1 text-center">
                        <input v-model="rows[i].nullable" type="checkbox" />
                      </td>
                    </template>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <Splitter
            :size="mappingWidth"
            :min="340"
            :max="900"
            orientation="vertical"
            @update:size="mappingWidth = $event"
          />

          <!-- Right pane: DDL preview (SQL Server) or operation preview (file) -->
          <section class="flex min-h-0 min-w-0 flex-1 flex-col">
            <div class="flex shrink-0 items-center justify-between px-3 pb-1 pt-3">
              <h3 class="text-2xs uppercase tracking-wide text-ink-subtle">
                {{ target === 'sqlserver' ? 'DDL preview' : 'Operation preview' }}
              </h3>
              <span v-if="target === 'sqlserver' && ifExists === 'append'" class="text-2xs text-warn">
                skipped in append mode
              </span>
            </div>
            <pre
              class="min-h-0 min-w-0 flex-1 cursor-text select-text overflow-auto whitespace-pre-wrap break-words px-3 pb-3 font-mono text-[11px] leading-snug text-ink"
              :class="{ 'opacity-50': target === 'sqlserver' && ifExists === 'append' }"
              :title="target === 'sqlserver' && ifExists === 'append' ? 'Append mode — DDL is not executed.  Shown for reference only.' : undefined"
            >{{ target === 'sqlserver' ? (ddl || '-- generating…') : fileOperationPreview }}</pre>
          </section>
        </div>
      </div>

      <!-- Footer -->
      <div class="flex shrink-0 flex-col gap-1.5 border-t border-border px-3 py-2">
        <!-- Phase strip + progress -->
        <div class="flex items-center gap-3 text-2xs">
          <span class="text-ink-subtle">phase:</span>
          <div class="flex items-center gap-1 font-mono">
            <template v-for="(step, i) in phaseSteps" :key="step">
              <span
                :class="{
                  'text-ink-subtle': phaseState(step) === 'pending',
                  'text-accent font-medium': phaseState(step) === 'active',
                  'text-success': phaseState(step) === 'done',
                  'text-danger font-medium': phaseState(step) === 'error',
                }"
              >{{ phaseLabel[step] }}</span>
              <span v-if="i < phaseSteps.length - 1" class="text-ink-subtle">·</span>
            </template>
          </div>
          <span v-if="progress && progress.phase === 'done'" class="text-success">
            ✓ {{ progress.rows_written.toLocaleString() }} rows in
            {{ (progress.elapsed_ms / 1000).toFixed(1) }}s
          </span>
          <span v-else-if="target === 'sqlserver' && progress && progress.total_rows > 0"
            class="text-ink-muted font-mono tabular-nums">
            {{ progress.rows_written.toLocaleString() }} / {{ progress.total_rows.toLocaleString() }} rows
            <span v-if="progress.chunk_no > 0" class="ml-1 text-ink-subtle">
              · chunk {{ progress.chunk_no }} ({{ progress.chunk_rows.toLocaleString() }} rows
              <span v-if="progress.last_chunk_ms > 0">in {{ (progress.last_chunk_ms / 1000).toFixed(2) }}s</span>)
            </span>
            <span class="ml-1 text-ink-subtle">· {{ (progress.elapsed_ms / 1000).toFixed(1) }}s</span>
          </span>
          <span v-else-if="target !== 'sqlserver' && exporting"
            class="font-mono tabular-nums text-ink-muted">
            writing… {{ ((progress?.elapsed_ms ?? 0) / 1000).toFixed(1) }}s
          </span>
        </div>

        <!-- Error block: scrollable + copyable so long ODBC errors
             aren't trapped in the 2-line footer slot. -->
        <div
          v-if="lastResult && !lastResult.ok"
          class="flex items-start gap-2 rounded border border-danger/40 bg-danger/5 px-2 py-1.5"
        >
          <div
            class="max-h-[140px] min-w-0 flex-1 select-text overflow-auto whitespace-pre-wrap break-words font-mono text-2xs"
          >
            <div class="text-danger">error: {{ lastResult.error }}</div>
            <div v-for="er in errorRows" :key="er.row" class="text-ink-muted">
              <span class="text-danger">✗</span>
              row {{ er.row.toLocaleString() }}
              <span v-if="er.marked.length" class="text-warn">
                [{{ er.marked.join(', ') }}]
              </span>
              — {{ er.message }}
            </div>
          </div>
          <button
            type="button"
            class="btn-icon shrink-0"
            title="Copy error"
            aria-label="Copy error"
            @click="copyError"
          >
            <Copy class="h-3.5 w-3.5" />
          </button>
        </div>

        <!-- Actions -->
        <div class="flex items-center gap-2">
          <button type="button" class="btn-secondary" :disabled="exporting" @click="$emit('close')">
            Close
          </button>
          <div class="ml-auto flex items-center gap-2">
            <button
              v-if="exporting"
              type="button"
              class="btn-secondary"
              @click="onCancelExport"
            >Cancel export</button>
            <button
              v-if="target === 'sqlserver'"
              type="button"
              class="btn-secondary"
              :disabled="!canExport"
              @click="refreshDdl"
              title="Regenerate DDL preview"
            >
              <Play class="h-3 w-3" />
              Preview
            </button>
            <button
              type="button"
              class="btn-primary"
              :disabled="!canExport"
              @click="onExport"
            >
              <Upload class="h-3 w-3" />
              Export
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
