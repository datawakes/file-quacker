<script setup lang="ts">
// Multi-sheet Excel chooser.  Shown after the user opens a .xlsx /
// .xlsm / .xls file that has more than one sheet, OR a single-sheet
// workbook where our auto-detect cropped a preamble (the user gets a
// chance to confirm or override the header row + start column).
//
// Outcomes:
//   - Just one sheet: load only the highlighted sheet.
//   - Load all: walk the sheet list, one workspace table per sheet.

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { File, Files, X } from 'lucide-vue-next'
import { useViewStore } from '../stores/view'
import { useTablesStore } from '../stores/tables'
import { useFocusTrap } from '../composables/useFocusTrap'
import type { ExcelSheetSpec } from '../lib/api'

const viewStore = useViewStore()
const tables = useTablesStore()
const { excelFile } = storeToRefs(viewStore)

const dialogRef = ref<HTMLElement | null>(null)
useFocusTrap(dialogRef)

function close() { viewStore.closeExcelFile() }
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') close() }
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

// Working copy; the auto-detected origin lands here, the user can
// edit it before loading.  `selected` toggles inclusion in the
// "Load selected sheets" action; `activeIdx` independently controls
// which sheet is shown in the preview pane.  Defaulting `selected`
// to true preserves the prior "load every sheet" behavior for users
// who don't change anything.
interface SheetEdit {
  name: string
  header_row: number
  start_col: string
  end_col: string
  detected: boolean
  preview: string[][]
  selected: boolean
}
const sheets = ref<SheetEdit[]>([])
const activeIdx = ref(0)

watch(excelFile, (info) => {
  if (!info) {
    sheets.value = []
    return
  }
  sheets.value = info.sheets.map(s => ({ ...s, selected: true }))
  activeIdx.value = 0
}, { immediate: true })

const active = computed(() => sheets.value[activeIdx.value] ?? null)
const thisPath = computed(() => excelFile.value?.this_path ?? '')
const selectedCount = computed(() => sheets.value.filter(s => s.selected).length)
const allSelected = computed(() =>
  sheets.value.length > 0 && sheets.value.every(s => s.selected),
)

function toggleAll() {
  const next = !allSelected.value
  for (const s of sheets.value) s.selected = next
}

function specsFor(list: SheetEdit[]): ExcelSheetSpec[] {
  return list.map(s => ({
    name: s.name,
    header_row: Math.max(1, Number(s.header_row) || 1),
    start_col: (s.start_col || 'A').toUpperCase(),
    end_col: (s.end_col || '').toUpperCase(),
  }))
}

async function loadOne() {
  if (!active.value) return close()
  const path = thisPath.value
  const specs = specsFor([active.value])
  close()
  await tables.loadExcelSheets(path, specs)
}

async function loadSelected() {
  const picked = sheets.value.filter(s => s.selected)
  if (!picked.length) return close()
  const path = thisPath.value
  const specs = specsFor(picked)
  close()
  await tables.loadExcelSheets(path, specs)
}

// Preview: if the user clicks a cell, set that as the new header row +
// start column.  end_col is recomputed from the rightmost non-empty
// cell on the new header row.
function clickCell(rowIdx0: number, colIdx0: number) {
  const a = active.value
  if (!a) return
  const row = a.preview[rowIdx0] ?? []
  const nonempty: number[] = []
  for (let i = 0; i < row.length; i++) if (row[i] !== '') nonempty.push(i)
  a.header_row = rowIdx0 + 1
  a.start_col = colLetter(colIdx0 + 1)
  a.end_col = nonempty.length ? colLetter(nonempty[nonempty.length - 1] + 1) : a.start_col
}

function colLetter(idx1: number): string {
  let n = idx1
  let out = ''
  while (n > 0) {
    const rem = (n - 1) % 26
    out = String.fromCharCode(65 + rem) + out
    n = Math.floor((n - 1) / 26)
  }
  return out || 'A'
}

// For preview rendering: highlight the header row + the data block
// starting at start_col through end_col.
function isHeader(rowIdx0: number): boolean {
  return active.value != null && rowIdx0 + 1 === active.value.header_row
}
function isInside(rowIdx0: number, colIdx0: number): boolean {
  if (!active.value) return false
  if (rowIdx0 + 1 < active.value.header_row) return false
  const startIdx = colIndex(active.value.start_col)
  const endIdx = active.value.end_col ? colIndex(active.value.end_col) : Number.MAX_SAFE_INTEGER
  return colIdx0 + 1 >= startIdx && colIdx0 + 1 <= endIdx
}
function colIndex(letter: string): number {
  let n = 0
  for (const ch of (letter || 'A').toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64)
  return n || 1
}
</script>

<template>
  <div
    v-if="excelFile"
    class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-[1px]"
  >
    <div
      ref="dialogRef"
      class="flex flex-col rounded-md border border-border bg-surface-1 shadow-xl"
      style="height: min(680px, 90vh); width: min(900px, 96vw);"
      role="dialog"
      aria-modal="true"
      aria-labelledby="excel-sheet-title"
    >
      <!-- Header -->
      <div class="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <Files class="h-4 w-4 text-ink-muted" />
        <h2 id="excel-sheet-title" class="text-sm font-medium text-ink">
          Excel workbook — {{ sheets.length }} sheet{{ sheets.length === 1 ? '' : 's' }}
        </h2>
        <button type="button" class="btn-icon ml-auto" aria-label="Close" title="Close" @click="close">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- Body: sheet list (left) + preview (right) -->
      <div class="flex min-h-0 flex-1">
        <!-- Sheets -->
        <div class="flex w-60 shrink-0 flex-col border-r border-border">
          <div class="flex shrink-0 items-center justify-between px-3 pb-1 pt-3">
            <span class="text-2xs uppercase tracking-wide text-ink-subtle">
              Sheets
            </span>
            <label
              class="flex items-center gap-1.5 text-2xs text-ink-subtle hover:text-ink cursor-pointer"
              :title="allSelected ? 'Deselect all' : 'Select all'"
            >
              <input
                type="checkbox"
                :checked="allSelected"
                @change="toggleAll"
              />
              <span>All</span>
            </label>
          </div>
          <ul class="min-h-0 flex-1 overflow-y-auto px-1 pb-2 text-xs">
            <li
              v-for="(s, i) in sheets"
              :key="s.name"
              class="flex items-center gap-2 rounded px-2 py-1"
              :class="i === activeIdx
                ? 'bg-accent/15 text-ink'
                : 'text-ink-secondary hover:bg-surface-2'"
            >
              <input
                type="checkbox"
                v-model="s.selected"
                class="shrink-0"
                :title="`Include ${s.name} in the load`"
                @click.stop
              />
              <button
                type="button"
                class="flex min-w-0 flex-1 items-center gap-2 text-left"
                @click="activeIdx = i"
              >
                <File class="h-3 w-3 shrink-0 text-ink-subtle" />
                <span class="truncate font-mono">{{ s.name }}</span>
                <span v-if="s.detected" class="ml-auto text-2xs text-warn" title="Preamble detected">
                  ✶
                </span>
              </button>
            </li>
          </ul>
        </div>

        <!-- Preview + origin controls -->
        <div class="flex min-h-0 min-w-0 flex-1 flex-col">
          <div class="flex shrink-0 items-center gap-3 px-3 pb-2 pt-3 text-xs">
            <span class="text-ink-muted">Header row</span>
            <input
              v-if="active"
              type="number"
              min="1"
              v-model.number="active.header_row"
              class="h-6 w-16 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
            />
            <span class="text-ink-muted">Start col</span>
            <input
              v-if="active"
              type="text"
              maxlength="3"
              v-model="active.start_col"
              class="h-6 w-12 rounded border border-border bg-surface-0 px-2 font-mono uppercase outline-none focus:border-accent"
            />
            <span class="text-ink-muted">End col</span>
            <input
              v-if="active"
              type="text"
              maxlength="3"
              v-model="active.end_col"
              class="h-6 w-12 rounded border border-border bg-surface-0 px-2 font-mono uppercase outline-none focus:border-accent"
            />
            <span v-if="active && active.detected" class="ml-auto text-2xs text-ink-subtle">
              Auto-detected — click a cell to move the data origin.
            </span>
          </div>

          <div class="min-h-0 min-w-0 flex-1 overflow-auto px-3 pb-3">
            <table v-if="active" class="border-collapse font-mono text-2xs">
              <thead>
                <tr>
                  <th class="sticky left-0 top-0 z-10 bg-surface-1 px-1.5 py-0.5 text-ink-subtle"></th>
                  <th
                    v-for="(_, j) in (active.preview[0] || [])"
                    :key="`h${j}`"
                    class="sticky top-0 bg-surface-1 px-1.5 py-0.5 text-ink-subtle"
                  >{{ colLetter(j + 1) }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, i) in active.preview" :key="`r${i}`">
                  <td class="sticky left-0 bg-surface-1 px-1.5 py-0.5 text-right text-ink-subtle">
                    {{ i + 1 }}
                  </td>
                  <td
                    v-for="(cell, j) in row"
                    :key="`c${i}-${j}`"
                    class="preview-cell cursor-pointer border border-border/60 px-1.5 py-0.5 text-ink"
                    :class="[
                      isHeader(i) && isInside(i, j) ? 'bg-accent/30 font-medium' : '',
                      !isHeader(i) && isInside(i, j) ? 'bg-accent/10' : '',
                      i + 1 < (active.header_row || 1) ? 'opacity-40' : '',
                    ]"
                    :title="cell"
                    @click="clickCell(i, j)"
                  >{{ cell }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="text-xs text-ink-subtle">
              Select a sheet to preview.
            </div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="flex h-12 shrink-0 items-center justify-end gap-2 border-t border-border px-3">
        <button type="button" class="btn-secondary" @click="close">Cancel</button>
        <button type="button" class="btn-secondary" :disabled="!active" @click="loadOne">
          Just this sheet
        </button>
        <button
          type="button"
          class="btn-primary"
          :disabled="selectedCount === 0"
          @click="loadSelected"
        >
          Load {{ selectedCount }} selected sheet{{ selectedCount === 1 ? '' : 's' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Cap each preview cell at a pragmatic width.  Long values (export
   metadata banners, generated-at timestamps, etc.) routinely run
   80+ chars and would otherwise blow the column out and push every
   other column off screen.  Truncate with an ellipsis; the full
   value is still readable via the hover tooltip and after the file
   loads.  ~22 monospace chars at our text-2xs is a happy medium
   between density and readability. */
.preview-cell {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
