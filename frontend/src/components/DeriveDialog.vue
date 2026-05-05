<script setup lang="ts">
// Derive a typed workspace table from a raw VARCHAR table.
//
// The dialog reads `suggest_casts` to pre-fill each column's target type
// based on which types the values parse cleanly as, then lets the user
// tweak per column, preview the result, and commit to a CREATE TABLE.

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { X, Play, Save as SaveIcon, Code2 } from 'lucide-vue-next'
import {
  createDerivedTable,
  deriveSql,
  previewDerive,
  suggestCasts,
  type CastSpec,
  type DerivePreview,
} from '../lib/api'
import { useToasts } from '../stores/toasts'
import { useTablesStore } from '../stores/tables'
import { useFocusTrap } from '../composables/useFocusTrap'

const dialogRef = ref<HTMLElement | null>(null)
useFocusTrap(dialogRef)

const props = defineProps<{ source: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'created', name: string): void }>()

const toasts = useToasts()
const tables = useTablesStore()

const TARGET_CHOICES: { value: string; label: string }[] = [
  { value: '',          label: 'Keep VARCHAR' },
  { value: 'BIGINT',    label: 'BIGINT' },
  { value: 'DECIMAL',   label: 'DECIMAL' },
  { value: 'DOUBLE',    label: 'DOUBLE' },
  { value: 'DATE',      label: 'DATE' },
  { value: 'TIMESTAMP', label: 'TIMESTAMP' },
  { value: 'BOOLEAN',   label: 'BOOLEAN' },
]

/** Standard dropdown + the suggested target if it's a non-standard
 *  form like `DECIMAL(6,2)`; shown just below "Keep VARCHAR". */
function choicesForRow(row: Row): { value: string; label: string }[] {
  if (!row.suggested) return TARGET_CHOICES
  if (TARGET_CHOICES.some(c => c.value === row.suggested)) return TARGET_CHOICES
  return [
    TARGET_CHOICES[0],
    { value: row.suggested, label: row.suggested },
    ...TARGET_CHOICES.slice(1),
  ]
}

interface Row {
  source: string
  source_type: string
  target: string
  cast_to: string
  suggested: string | null
  coverage: Record<string, number>
  sample: string[]
  strict: boolean
  /** Carried from `suggest_casts` for non-ISO DATE / TIMESTAMP values;
   *  forwarded to the backend so it can use `try_strptime(col, fmt)`. */
  date_format: string | null
}

const rows = ref<Row[]>([])
const newTableName = ref(`${props.source}_typed`)
const whereClause = ref('')
const preview = ref<DerivePreview | null>(null)
const shownSql = ref<string | null>(null)
type PanelView = 'empty' | 'preview' | 'sql'
const panelView = ref<PanelView>('empty')
const loading = ref(true)
const busy = ref(false)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const suggestions = await suggestCasts(props.source)
    rows.value = suggestions
      .filter(s => s.source !== '_src_row_num')
      .map(s => ({
        source: s.source,
        source_type: s.source_type,
        target: s.source,
        cast_to: s.suggested_type ?? '',
        suggested: s.suggested_type,
        coverage: s.cast_coverage,
        sample: s.sample_values,
        strict: false,
        date_format: s.detected_date_format,
      }))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})

function specsFromRows(): CastSpec[] {
  return rows.value.map(r => {
    // Only forward `date_format` when the user kept the suggested DATE /
    // TIMESTAMP target; if they switched the type, the format no longer
    // applies.  ISO formats are signalled by a null `date_format` and
    // fall through to plain `try_cast` on the backend.
    const up = r.cast_to.trim().toUpperCase()
    const useFmt = (up === 'DATE' || up === 'TIMESTAMP') && r.cast_to === (r.suggested ?? '')
    return {
      source: r.source,
      target: r.target.trim() || r.source,
      cast_to: r.cast_to || null,
      strict: r.strict,
      date_format: useFmt ? r.date_format : null,
    }
  })
}

async function onPreview() {
  busy.value = true
  error.value = null
  try {
    preview.value = await previewDerive(props.source, specsFromRows(), whereClause.value || null, 20)
    panelView.value = 'preview'
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    preview.value = null
  } finally {
    busy.value = false
  }
}

async function onShowSql() {
  const name = newTableName.value.trim() || `${props.source}_typed`
  busy.value = true
  error.value = null
  try {
    const r = await deriveSql(props.source, name, specsFromRows(), whereClause.value || null)
    shownSql.value = r.sql
    panelView.value = 'sql'
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return 'NULL'
  return String(v)
}

async function onCommit() {
  const name = newTableName.value.trim()
  if (!name) {
    toasts.show('Name the derived table first.', 'warn')
    return
  }
  busy.value = true
  error.value = null
  try {
    const r = await createDerivedTable(props.source, name, specsFromRows(), whereClause.value || null)
    if (r.ok) {
      toasts.show(`Created “${r.name}” with ${r.row_count.toLocaleString()} rows`, 'success')
      await tables.refresh()
      emit('created', r.name)
      emit('close')
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

function coverageFor(row: Row): number | null {
  if (!row.cast_to) return null
  // `DECIMAL(6,2)` lives in coverage as the bare key `DECIMAL`.
  const key = row.cast_to.replace(/\(.*/, '')
  return row.coverage[key] ?? null
}

const typedCount = computed(() => rows.value.filter(r => r.cast_to).length)

// Source column is user-resizable so long sample strings don't push the
// rest off-screen.  Default width leaves room for Cast to / Target name
// / Clean % at a sensible balance; user drags the header edge to adjust.
const SOURCE_MIN = 120
const SOURCE_MAX = 520
const sourceColWidth = ref(220)

// Sum of column widths; drives table min-width so Target name and
// Clean % never collapse when the left pane is narrower than needed.
// Horizontal scroll kicks in on the container automatically.
const totalTableWidth = computed(() => sourceColWidth.value + 160 + 200 + 64 + 16)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    const t = e.target as HTMLElement | null
    const tag = t?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t?.isContentEditable) {
      (t as HTMLElement).blur()
      return
    }
    emit('close')
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

function onSourceResizeStart(ev: PointerEvent) {
  ev.preventDefault()
  ev.stopPropagation()
  const startX = ev.clientX
  const startW = sourceColWidth.value
  const el = ev.target as HTMLElement
  el.setPointerCapture?.(ev.pointerId)

  function onMove(e: PointerEvent) {
    sourceColWidth.value = Math.max(SOURCE_MIN, Math.min(SOURCE_MAX, startW + (e.clientX - startX)))
  }
  function onUp(e: PointerEvent) {
    el.releasePointerCapture?.(e.pointerId)
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
</script>

<template>
  <!-- Modal backdrop -->
  <!-- Intentionally no click-outside close: a user in the middle of
       renaming dozens of target columns shouldn't lose their work to a
       stray click.  Close / Cancel / Esc are the explicit ways out. -->
  <div
    class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-[1px]"
  >
    <!-- Dialog -->
    <div
      ref="dialogRef"
      class="flex h-[80vh] w-[min(980px,94vw)] flex-col rounded-md border border-border bg-surface-1 shadow-xl"
      role="dialog"
      aria-modal="true"
      aria-labelledby="derive-title"
    >
      <!-- Header -->
      <div class="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <h2 id="derive-title" class="text-sm font-medium text-ink">
          Clone typed table
          <span class="ml-1 font-mono text-ink-muted">from {{ source }}</span>
        </h2>
        <span v-if="!loading" class="text-2xs text-ink-subtle">
          {{ typedCount }}/{{ rows.length }} columns cast
        </span>
        <button type="button" class="btn-icon ml-auto" aria-label="Close" title="Close" @click="$emit('close')">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- Body -->
      <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(0,1fr)] divide-x divide-border">
        <!-- Left: per-column cast builder -->
        <div class="flex min-h-0 flex-col">
          <div class="min-h-0 flex-1 overflow-auto px-3 pb-2 pt-3 text-xs">
            <div v-if="loading" class="text-ink-subtle">Analyzing columns…</div>
            <div v-else-if="error" class="font-mono text-danger whitespace-pre-wrap">{{ error }}</div>
            <template v-else>
              <table
                class="table-fixed border-collapse"
                :style="{ minWidth: totalTableWidth + 'px', width: totalTableWidth + 'px' }"
              >
                <colgroup>
                  <col :style="{ width: sourceColWidth + 'px' }" />
                  <col style="width: 160px" />
                  <col style="width: 200px" />
                  <col style="width: 64px" />
                </colgroup>
                <thead>
                  <tr class="sticky top-0 bg-surface-1 text-left text-2xs uppercase tracking-wide text-ink-subtle">
                    <th class="relative py-1.5 pr-2 font-medium">
                      Source
                      <div
                        class="col-resize"
                        role="separator"
                        aria-orientation="vertical"
                        title="Drag to resize"
                        @pointerdown="onSourceResizeStart"
                      />
                    </th>
                    <th class="py-1.5 pr-2 font-medium">Cast to</th>
                    <th class="py-1.5 pr-2 font-medium">Target name</th>
                    <th class="py-1.5 pr-2 font-medium text-right">Clean %</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(r, i) in rows"
                    :key="r.source"
                    class="border-t border-border/50 align-top"
                  >
                    <td class="py-1.5 pr-2">
                      <div class="truncate font-mono text-ink" :title="r.source">{{ r.source }}</div>
                      <div class="truncate font-mono text-[10px] text-ink-subtle" :title="r.sample.join(' · ')">
                        {{ r.sample.slice(0, 3).join(' · ') || '—' }}
                      </div>
                    </td>
                    <td class="py-1.5 pr-2">
                      <select
                        v-model="rows[i].cast_to"
                        class="h-6 w-full rounded border border-border bg-surface-0 px-1 font-mono text-xs text-ink outline-none focus:border-accent"
                      >
                        <option
                          v-for="opt in choicesForRow(r)"
                          :key="opt.value"
                          :value="opt.value"
                        >{{ opt.label }}</option>
                      </select>
                    </td>
                    <td class="py-1.5 pr-2">
                      <input
                        v-model="rows[i].target"
                        type="text"
                        class="h-6 w-full rounded border border-border bg-surface-0 px-2 font-mono text-xs text-ink outline-none focus:border-accent"
                      />
                    </td>
                    <td class="py-1.5 pr-2 text-right font-mono tabular-nums">
                      <span
                        v-if="coverageFor(r) !== null"
                        :class="coverageFor(r)! >= 95 ? 'text-success' : coverageFor(r)! >= 70 ? 'text-warn' : 'text-ink-subtle'"
                      >{{ coverageFor(r)!.toFixed(0) }}%</span>
                      <span v-else class="text-ink-subtle">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </template>
          </div>
        </div>

        <!-- Right: preview + options -->
        <div class="flex min-h-0 flex-col">
          <!-- Options -->
          <div class="shrink-0 space-y-2 border-b border-border px-3 py-3 text-xs">
            <div class="flex items-center gap-2">
              <label class="w-[110px] text-ink-muted">New table name</label>
              <input
                v-model="newTableName"
                type="text"
                class="h-6 flex-1 rounded border border-border bg-surface-0 px-2 font-mono text-xs text-ink outline-none focus:border-accent"
              />
            </div>
            <div class="flex items-start gap-2">
              <label class="mt-1 w-[110px] text-ink-muted">WHERE</label>
              <textarea
                v-model="whereClause"
                rows="2"
                placeholder="optional — e.g.  status = 'active'"
                class="flex-1 rounded border border-border bg-surface-0 px-2 py-1 font-mono text-xs text-ink outline-none focus:border-accent"
              />
            </div>
          </div>

          <!-- Preview / SQL -->
          <div class="min-h-0 flex-1 overflow-auto">
            <table
              v-if="panelView === 'preview' && preview"
              class="w-full border-collapse font-mono text-2xs"
            >
              <thead>
                <tr class="sticky top-0 bg-surface-1 text-left uppercase tracking-wide text-ink-subtle">
                  <th
                    v-for="col in preview.columns"
                    :key="col.name"
                    class="whitespace-nowrap border-b border-border px-2 py-1 font-medium"
                    :title="`${col.name} · ${col.type}`"
                  >
                    {{ col.name }}
                    <span class="ml-1 font-normal text-ink-subtle/70">{{ col.type }}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in preview.rows" :key="ri" class="border-b border-border/40">
                  <td
                    v-for="(v, ci) in row"
                    :key="ci"
                    class="truncate max-w-[240px] whitespace-nowrap px-2 py-1"
                    :title="fmtCell(v)"
                  >
                    <span v-if="v === null" class="italic text-ink-subtle">NULL</span>
                    <span v-else>{{ fmtCell(v) }}</span>
                  </td>
                </tr>
                <tr v-if="!preview.rows.length">
                  <td :colspan="preview.columns.length" class="px-2 py-3 text-center text-ink-subtle">
                    No rows match.
                  </td>
                </tr>
              </tbody>
            </table>
            <pre
              v-else-if="panelView === 'sql' && shownSql"
              class="m-0 select-text whitespace-pre px-3 py-3 font-mono text-xs leading-5 text-ink"
            >{{ shownSql }}</pre>
            <div v-else class="flex h-full items-center justify-center px-3 text-center text-xs text-ink-subtle">
              Click <span class="mx-1 font-mono">Preview</span> to see 20 rows,
              or <span class="mx-1 font-mono">Show SQL</span> to see the CREATE TABLE.
            </div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="flex h-11 shrink-0 items-center gap-2 border-t border-border px-3">
        <span v-if="error && !loading" class="truncate font-mono text-xs text-danger" :title="error">{{ error }}</span>
        <button type="button" class="btn-secondary ml-auto" :disabled="busy" @click="$emit('close')">
          Cancel
        </button>
        <button type="button" class="btn-secondary" :disabled="busy || loading" @click="onPreview">
          <Play class="h-3 w-3" />
          Preview
        </button>
        <button type="button" class="btn-secondary" :disabled="busy || loading" @click="onShowSql">
          <Code2 class="h-3 w-3" />
          Show SQL
        </button>
        <button type="button" class="btn-primary" :disabled="busy || loading" @click="onCommit">
          <SaveIcon class="h-3 w-3" />
          Create table
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Drag handle for the Source column; thin, invisible until hover. */
.col-resize {
  position: absolute;
  top: 0;
  right: -3px;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  z-index: 1;
}
.col-resize:hover,
.col-resize:active {
  background: linear-gradient(to right, transparent 0 2px, rgb(var(--accent)) 2px 4px, transparent 4px 6px);
}
</style>
