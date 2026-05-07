<script setup lang="ts">
// Virtualized grid driven by a GridSource adapter.  Behavior shared across
// both the loaded-tables view and the SQL-results view: windowed row
// fetches, click-to-sort headers, drag-extend cell selection, Ctrl+C-as-TSV,
// right-click context menu (Copy / Pivot Row), resizable columns,
// auto-fit on double-click.

import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { ArrowDown, ArrowUp, BarChart3 } from 'lucide-vue-next'
import { useViewStore } from '../stores/view'
import type { Cell, GridSource } from './grid/types'
import {
  CELL_PAD,
  CHAR_W,
  COL_MAX,
  COL_MIN,
  FETCH_DEBOUNCE_MS,
  HEADER_ICON_BUDGET,
  ROW_H,
  ROWNUM_W_MIN,
  SAMPLE_ROWS,
} from '../lib/constants'

const props = defineProps<{
  source: GridSource
}>()

const parentRef = ref<HTMLDivElement | null>(null)

// ---- virtualizer ------------------------------------------------------ //
// Sorted mode counts the in-memory sorted rows; unsorted counts the full
// table and uses windowed fetches.
const virtualizer = useVirtualizer(
  computed(() => ({
    count: props.source.visibleRowCount.value,
    getScrollElement: () => parentRef.value,
    estimateSize: () => ROW_H,
    overscan: 12,
  })),
)
const virtualRows = computed(() => virtualizer.value.getVirtualItems())
const totalSize = computed(() => virtualizer.value.getTotalSize())

// ---- windowed fetch on scroll ---------------------------------------- //
let fetchTimer: number | undefined
let lastTargetStart = -1

function scheduleWindowFetch(absIndex: number) {
  const w = props.source.rowWindow.value
  if (w && absIndex >= w.absStart && absIndex < w.absStart + w.rows.length) return

  const total = props.source.totalRows.value
  const size = props.source.windowSize.value
  const half = Math.floor(size / 2)
  const targetStart = Math.max(0, Math.min(absIndex - half, Math.max(0, total - size)))
  if (targetStart === lastTargetStart) return
  lastTargetStart = targetStart

  if (fetchTimer) window.clearTimeout(fetchTimer)
  fetchTimer = window.setTimeout(() => void props.source.loadWindow(targetStart), FETCH_DEBOUNCE_MS)
}

watch(virtualRows, (items) => {
  // Sorted mode keeps the full set in memory; no fetches.
  if (props.source.sortedRows.value) return
  if (!items.length) return
  const first = items[0].index
  const last = items[items.length - 1].index
  const w = props.source.rowWindow.value
  if (w && first >= w.absStart && last < w.absStart + w.rows.length) return
  scheduleWindowFetch(Math.floor((first + last) / 2))
})

// ---- row lookup ------------------------------------------------------- //
function rowAt(absIndex: number): Cell[] | null {
  const sorted = props.source.sortedRows.value
  if (sorted) return sorted[absIndex] ?? null
  const w = props.source.rowWindow.value
  if (!w) return null
  const local = absIndex - w.absStart
  if (local < 0 || local >= w.rows.length) return null
  return w.rows[local]
}

// ---- column widths: auto-sized + user overrides --------------------- //
const userColWidths = reactive<Record<string, number>>({})

watch(() => props.source.resetKey.value, () => {
  for (const k of Object.keys(userColWidths)) delete userColWidths[k]
})

const autoColWidths = computed<number[]>(() => {
  const cols = props.source.displayCols.value
  if (!cols.length) return []
  const sample = (props.source.rowWindow.value?.rows ?? []).slice(0, SAMPLE_ROWS)
  return cols.map(col => {
    let maxPx = col.name.length * CHAR_W + CELL_PAD + HEADER_ICON_BUDGET
    for (const row of sample) {
      const v = row?.[col.origIdx]
      const len = v == null ? 4 : String(v).length
      const px = len * CHAR_W + CELL_PAD
      if (px > maxPx) maxPx = px
    }
    return Math.max(COL_MIN, Math.min(COL_MAX, maxPx))
  })
})

const effectiveColWidths = computed<number[]>(() =>
  props.source.displayCols.value.map((col, i) =>
    userColWidths[col.name] ?? autoColWidths.value[i] ?? COL_MIN),
)

// Row-number column widens with row count so 100k+ digits don't clip.
const rownumWidth = computed(() => {
  const n = Math.max(1, props.source.visibleRowCount.value)
  const digits = n.toLocaleString().length
  return Math.max(ROWNUM_W_MIN, Math.ceil(digits * CHAR_W + CELL_PAD))
})

const gridTemplate = computed(() => {
  const parts = [`${rownumWidth.value}px`, ...effectiveColWidths.value.map(w => `${w}px`)]
  return parts.join(' ')
})

// ---- column resize ---------------------------------------------------- //
function onResizeStart(colName: string, colIndex: number, ev: PointerEvent) {
  ev.preventDefault()
  ev.stopPropagation()
  const startX = ev.clientX
  const startW = effectiveColWidths.value[colIndex] ?? COL_MIN
  const el = ev.target as HTMLElement
  el.setPointerCapture?.(ev.pointerId)

  function onMove(e: PointerEvent) {
    userColWidths[colName] = Math.max(COL_MIN, startW + (e.clientX - startX))
  }
  function onUp(e: PointerEvent) {
    el.releasePointerCapture?.(e.pointerId)
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}

// Double-click auto-fits to the widest sampled value with no cap, so the
// user can reveal long content in full.
function onResizeReset(colName: string, colIndex: number, ev: MouseEvent) {
  ev.preventDefault()
  ev.stopPropagation()
  const col = props.source.displayCols.value[colIndex]
  if (!col) return
  const sample = (props.source.rowWindow.value?.rows ?? []).slice(0, SAMPLE_ROWS)
  let maxPx = col.name.length * CHAR_W + CELL_PAD + HEADER_ICON_BUDGET
  for (const row of sample) {
    const v = row?.[col.origIdx]
    const len = v == null ? 4 : String(v).length
    const px = len * CHAR_W + CELL_PAD
    if (px > maxPx) maxPx = px
  }
  userColWidths[colName] = Math.max(COL_MIN, maxPx)
}

// ---- selection + copy ------------------------------------------------ //
// Normalized rectangle in (absolute row, display-col) coordinates.
// `anchor` is the fixed corner during shift/drag extends.  `selectionMode
// = 'row'` keeps drags from the row-number column locked to full-width
// selections.
interface CellPos { row: number; col: number }
interface Range { r0: number; r1: number; c0: number; c1: number }

const anchor = ref<CellPos | null>(null)
const selection = ref<Range | null>(null)
const selectionMode = ref<'cell' | 'row'>('cell')
const dragging = ref<'cell' | 'row' | null>(null)
const flashing = ref(false)
const contextMenu = ref<{ x: number; y: number; kind: 'cell' | 'rownum' } | null>(null)

function normalize(a: CellPos, b: CellPos): Range {
  return {
    r0: Math.min(a.row, b.row),
    r1: Math.max(a.row, b.row),
    c0: Math.min(a.col, b.col),
    c1: Math.max(a.col, b.col),
  }
}

function inSelection(row: number, colDisp: number): boolean {
  const s = selection.value
  if (!s) return false
  return row >= s.r0 && row <= s.r1 && colDisp >= s.c0 && colDisp <= s.c1
}

const lastDispCol = computed(() => Math.max(0, props.source.displayCols.value.length - 1))

function setRowSelection(r0: number, r1: number) {
  selection.value = { r0: Math.min(r0, r1), r1: Math.max(r0, r1), c0: 0, c1: lastDispCol.value }
  selectionMode.value = 'row'
}

// Without this blur, CodeMirror keeps contenteditable focus and the
// global Ctrl+C handler bails before reaching the cell-copy path.
function blurActiveEditor() {
  if (!props.source.blurOnMouseDown) return
  const a = document.activeElement as HTMLElement | null
  if (a?.isContentEditable || a?.tagName === 'INPUT' || a?.tagName === 'TEXTAREA') {
    a.blur()
  }
}

function onCellMouseDown(rowIdx: number, colDisp: number, e: MouseEvent) {
  if (e.button !== 0) return
  if (props.source.skipUnloadedRows && rowAt(rowIdx) == null) return
  e.preventDefault()
  blurActiveEditor()
  // Drop any leftover text selection from elsewhere on the page.
  // mousedown.preventDefault() blocks the browser's default selection-clear,
  // so without this a stale text selection (SQL editor, sidebar, etc.)
  // would steal the Ctrl+C handler away from the grid.
  window.getSelection()?.removeAllRanges()
  contextMenu.value = null
  if (e.shiftKey && anchor.value) {
    selection.value = normalize(anchor.value, { row: rowIdx, col: colDisp })
    selectionMode.value = 'cell'
    return
  }
  anchor.value = { row: rowIdx, col: colDisp }
  selection.value = { r0: rowIdx, r1: rowIdx, c0: colDisp, c1: colDisp }
  selectionMode.value = 'cell'
  dragging.value = 'cell'
}

function onCellMouseEnter(rowIdx: number, colDisp: number) {
  if (dragging.value !== 'cell' || !anchor.value) return
  selection.value = normalize(anchor.value, { row: rowIdx, col: colDisp })
}

function onRowNumMouseDown(rowIdx: number, e: MouseEvent) {
  if (e.button !== 0) return
  e.preventDefault()
  blurActiveEditor()
  window.getSelection()?.removeAllRanges()
  contextMenu.value = null
  if (e.shiftKey && anchor.value) {
    setRowSelection(anchor.value.row, rowIdx)
    return
  }
  anchor.value = { row: rowIdx, col: 0 }
  setRowSelection(rowIdx, rowIdx)
  dragging.value = 'row'
}

function onRowNumMouseEnter(rowIdx: number) {
  if (dragging.value !== 'row' || !anchor.value) return
  setRowSelection(anchor.value.row, rowIdx)
}

function onWindowMouseUp() {
  dragging.value = null
  stopAutoScroll()
}

// ---- autoscroll while drag-selecting past the viewport edge --------- //
// mousemove tracks the cursor; a rAF loop scrolls the parent container
// whenever the cursor sits inside the edge band. Current browsers fire
// mouseenter on rows that scroll under a stationary cursor, so the
// existing selection-extend logic just keeps working as new rows pass
// under the pointer.
const AUTOSCROLL_MARGIN = 24
const AUTOSCROLL_MAX_STEP = 24
let autoScrollFrame: number | undefined
let lastMouseX = 0
let lastMouseY = 0

function onWindowMouseMove(e: MouseEvent) {
  if (!dragging.value) return
  lastMouseX = e.clientX
  lastMouseY = e.clientY
  if (autoScrollFrame == null) scheduleAutoScroll()
}

function scheduleAutoScroll() {
  autoScrollFrame = window.requestAnimationFrame(() => {
    autoScrollFrame = undefined
    if (!dragging.value) return
    const el = parentRef.value
    if (!el) return
    const rect = el.getBoundingClientRect()
    const step = (overshoot: number) =>
      Math.min(AUTOSCROLL_MAX_STEP, Math.ceil(overshoot / 2))

    let dy = 0
    if (lastMouseY < rect.top + AUTOSCROLL_MARGIN) {
      dy = -step(rect.top + AUTOSCROLL_MARGIN - lastMouseY)
    } else if (lastMouseY > rect.bottom - AUTOSCROLL_MARGIN) {
      dy = step(lastMouseY - (rect.bottom - AUTOSCROLL_MARGIN))
    }
    let dx = 0
    if (lastMouseX < rect.left + AUTOSCROLL_MARGIN) {
      dx = -step(rect.left + AUTOSCROLL_MARGIN - lastMouseX)
    } else if (lastMouseX > rect.right - AUTOSCROLL_MARGIN) {
      dx = step(lastMouseX - (rect.right - AUTOSCROLL_MARGIN))
    }
    if (dx !== 0 || dy !== 0) {
      el.scrollTop += dy
      el.scrollLeft += dx
      scheduleAutoScroll()
    }
  })
}

function stopAutoScroll() {
  if (autoScrollFrame != null) {
    window.cancelAnimationFrame(autoScrollFrame)
    autoScrollFrame = undefined
  }
}

function onCellContextMenu(rowIdx: number, colDisp: number, e: MouseEvent) {
  if (props.source.skipUnloadedRows && rowAt(rowIdx) == null) return
  e.preventDefault()
  blurActiveEditor()
  if (!inSelection(rowIdx, colDisp)) {
    anchor.value = { row: rowIdx, col: colDisp }
    selection.value = { r0: rowIdx, r1: rowIdx, c0: colDisp, c1: colDisp }
    selectionMode.value = 'cell'
  }
  contextMenu.value = { x: e.clientX, y: e.clientY, kind: 'cell' }
}

function onRowNumContextMenu(rowIdx: number, e: MouseEvent) {
  e.preventDefault()
  blurActiveEditor()
  const inRowSel = selection.value
    && selectionMode.value === 'row'
    && rowIdx >= selection.value.r0
    && rowIdx <= selection.value.r1
  if (!inRowSel) {
    anchor.value = { row: rowIdx, col: 0 }
    setRowSelection(rowIdx, rowIdx)
  }
  contextMenu.value = { x: e.clientX, y: e.clientY, kind: 'rownum' }
}

function closeContextMenu() { contextMenu.value = null }

function onPivotFromMenu() {
  const s = selection.value
  if (s) props.source.onPivotRow(s.r0)
  closeContextMenu()
}

// ---- TSV serialization + copy --------------------------------------- //
function serializeTSV(includeHeaders: boolean): string {
  const s = selection.value
  if (!s) return ''
  const cols = props.source.displayCols.value
  const lines: string[] = []
  if (includeHeaders) {
    const names: string[] = []
    for (let c = s.c0; c <= s.c1; c++) names.push(cols[c]?.displayName ?? '')
    lines.push(names.join('\t'))
  }
  // Single-cell, no headers: emit the raw value with no separators.
  if (!includeHeaders && s.r0 === s.r1 && s.c0 === s.c1) {
    const row = rowAt(s.r0)
    const v = row?.[cols[s.c0]?.origIdx ?? -1]
    return v == null ? '' : String(v)
  }
  for (let r = s.r0; r <= s.r1; r++) {
    const row = rowAt(r)
    const cells: string[] = []
    for (let c = s.c0; c <= s.c1; c++) {
      const orig = cols[c]?.origIdx ?? -1
      const v = row?.[orig]
      const text = v == null ? '' : String(v).replace(/[\t\r\n]/g, ' ')
      cells.push(text)
    }
    lines.push(cells.join('\t'))
  }
  return lines.join('\n')
}

function serializeHeaders(): string {
  const s = selection.value
  if (!s) return ''
  const cols = props.source.displayCols.value
  const names: string[] = []
  for (let c = s.c0; c <= s.c1; c++) names.push(cols[c]?.displayName ?? '')
  return names.join('\t')
}

async function writeToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    flashing.value = true
    window.setTimeout(() => { flashing.value = false }, 300)
  } catch (e) {
    console.warn('[Grid] copy failed:', e)
  }
  closeContextMenu()
}

async function copyAsTSV(includeHeaders: boolean) {
  if (!selection.value) return
  await writeToClipboard(serializeTSV(includeHeaders))
}

async function copyHeadersOnly() {
  if (!selection.value) return
  await writeToClipboard(serializeHeaders())
}

function onKeydown(e: KeyboardEvent) {
  // Don't intercept Ctrl+C in inputs; the browser handles text copy there.
  const t = e.target as HTMLElement | null
  const tag = t?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return

  if ((e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'C')) {
    // Defer to the browser when there's live document text selection
    // (e.g. in the pivot panel) so copy lands the text selection rather
    // than the grid range.
    const docSel = window.getSelection()
    if (docSel && docSel.toString().length > 0) return
    if (!selection.value) return
    e.preventDefault()
    void copyAsTSV(false)
    return
  }
  if (e.key === 'Escape') {
    if (contextMenu.value) { closeContextMenu(); return }
    if (selection.value) { selection.value = null; anchor.value = null }
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('mouseup', onWindowMouseUp)
  window.addEventListener('mousemove', onWindowMouseMove)
  // Outside-click closes the context menu.  contextmenu doesn't fire
  // 'click', so opening a menu won't immediately close it.
  window.addEventListener('click', closeContextMenu)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('mouseup', onWindowMouseUp)
  window.removeEventListener('mousemove', onWindowMouseMove)
  window.removeEventListener('click', closeContextMenu)
  stopAutoScroll()
})

// Reset selection + scroll when the underlying data set changes.
watch(() => props.source.resetKey.value, async () => {
  selection.value = null
  anchor.value = null
  contextMenu.value = null
  lastTargetStart = -1
  await nextTick()
  parentRef.value?.scrollTo({ top: 0 })
})

// Sort change: scroll back to the top so the user sees row 1 of the new
// order.  Tables-mode store reloads on sort clear, so this is harmless
// there too.
watch(() => props.source.sort.value, async () => {
  await nextTick()
  parentRef.value?.scrollTo({ top: 0 })
})

// ---- jump to row ------------------------------------------------------ //
const viewStore = useViewStore()
const { jumpTick, pendingJumpRow, mode: viewMode } = storeToRefs(viewStore)

watch(jumpTick, async () => {
  if (!props.source.shouldHonorJump(viewMode.value)) return
  const n = pendingJumpRow.value
  if (n == null) return
  const total = props.source.visibleRowCount.value
  if (total === 0) return
  const idx = Math.max(0, Math.min(n - 1, total - 1))
  await props.source.jumpTo(idx)
  lastTargetStart = props.source.rowWindow.value?.absStart ?? -1
  await nextTick()
  virtualizer.value.scrollToIndex(idx, { align: 'start' })
})

// ---- cell formatting -------------------------------------------------- //
function fmtCell(v: unknown): { text: string; isNull: boolean } {
  if (v === null || v === undefined) return { text: 'NULL', isNull: true }
  return { text: String(v), isNull: false }
}
</script>

<template>
  <div class="contents">
    <div
      ref="parentRef"
      class="min-h-0 min-w-0 flex-1 overflow-auto"
      role="grid"
    >
      <div class="inline-grid" :style="{ gridTemplateColumns: gridTemplate }">
        <!-- Header row: click to sort, drag right edge to resize. -->
        <div
          class="sticky top-0 z-20 col-span-full grid bg-surface-1"
          :style="{ gridTemplateColumns: gridTemplate }"
        >
          <div class="cell cell-head cell-rownum">#</div>
          <div
            v-for="(col, ci) in props.source.displayCols.value"
            :key="col.name"
            class="cell cell-head group relative cursor-pointer select-none"
            :title="`${col.displayName} · ${col.type} — click to sort`"
            @click="props.source.toggleSort(col.name)"
          >
            <div class="flex min-w-0 w-full items-center gap-1">
              <span class="min-w-0 font-medium text-ink overflow-hidden whitespace-nowrap">{{ col.displayName }}</span>
              <ArrowUp
                v-if="props.source.sort.value && props.source.sort.value.column === col.name && props.source.sort.value.dir === 'asc'"
                class="h-3 w-3 shrink-0 text-accent"
              />
              <ArrowDown
                v-else-if="props.source.sort.value && props.source.sort.value.column === col.name && props.source.sort.value.dir === 'desc'"
                class="h-3 w-3 shrink-0 text-accent"
              />
              <button
                type="button"
                class="ml-auto h-4 w-4 shrink-0 rounded opacity-0 transition-opacity group-hover:opacity-60 hover:!opacity-100 hover:text-accent"
                :title="`Profile ${col.name}`"
                :aria-label="`Profile ${col.name}`"
                @click.stop="props.source.onProfileColumn(col.name)"
              >
                <BarChart3 class="h-3 w-3" />
              </button>
            </div>
            <div
              class="col-resize"
              role="separator"
              aria-orientation="vertical"
              title="Drag to resize · double-click to auto-fit"
              @pointerdown="onResizeStart(col.name, ci, $event)"
              @dblclick="onResizeReset(col.name, ci, $event)"
              @click.stop
            />
          </div>
        </div>

        <!-- Virtualized body. -->
        <div class="relative col-span-full" :style="{ height: `${totalSize}px` }">
          <div
            v-for="vRow in virtualRows"
            :key="String(vRow.key)"
            class="absolute left-0 right-0 grid"
            :style="{
              transform: `translateY(${vRow.start}px)`,
              gridTemplateColumns: gridTemplate,
              height: `${ROW_H}px`,
            }"
          >
            <div
              class="cell cell-rownum cell-rownum-click font-mono tabular-nums text-ink-subtle"
              :class="inSelection(vRow.index, 0) ? 'bg-accent/15' : ''"
              :title="`Click to select row · right-click for more`"
              @mousedown="onRowNumMouseDown(vRow.index, $event)"
              @mouseenter="onRowNumMouseEnter(vRow.index)"
              @contextmenu="onRowNumContextMenu(vRow.index, $event)"
            >
              {{ (vRow.index + 1).toLocaleString() }}
            </div>
            <template v-if="rowAt(vRow.index)">
              <div
                v-for="(col, ci) in props.source.displayCols.value"
                :key="col.name"
                class="cell font-mono transition-colors"
                :class="[
                  inSelection(vRow.index, ci)
                    ? (flashing ? 'bg-accent/30' : 'bg-accent/15')
                    : 'hover:bg-surface-2/60',
                ]"
                :title="fmtCell(rowAt(vRow.index)![col.origIdx]).text"
                @mousedown="onCellMouseDown(vRow.index, ci, $event)"
                @mouseenter="onCellMouseEnter(vRow.index, ci)"
                @contextmenu="onCellContextMenu(vRow.index, ci, $event)"
              >
                <span v-if="fmtCell(rowAt(vRow.index)![col.origIdx]).isNull" class="italic text-ink-subtle">NULL</span>
                <span v-else class="overflow-hidden whitespace-nowrap">{{ fmtCell(rowAt(vRow.index)![col.origIdx]).text }}</span>
              </div>
            </template>
            <template v-else-if="props.source.rowPlaceholder === 'block'">
              <div v-for="col in props.source.displayCols.value" :key="col.name" class="cell">
                <span class="text-ink-subtle/60">—</span>
              </div>
            </template>
            <template v-else>
              <div
                v-for="(col, ci) in props.source.displayCols.value"
                :key="col.name"
                class="cell font-mono text-ink-subtle/40"
                title="loading…"
                @mousedown="onCellMouseDown(vRow.index, ci, $event)"
                @mouseenter="onCellMouseEnter(vRow.index, ci)"
                @contextmenu="onCellContextMenu(vRow.index, ci, $event)"
              >
                —
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>

    <slot name="overlay" />

    <!-- Right-click context menu (positioned at click coords). -->
    <div
      v-if="contextMenu"
      class="fq-ctx-menu"
      :style="{ top: `${contextMenu.y}px`, left: `${contextMenu.x}px` }"
      @click.stop
      @contextmenu.prevent
    >
      <button v-if="contextMenu.kind === 'rownum'" class="fq-ctx-item" @click="onPivotFromMenu()">
        Pivot Row
      </button>
      <div v-if="contextMenu.kind === 'rownum'" class="fq-ctx-sep" />
      <button class="fq-ctx-item" @click="copyAsTSV(false)">
        Copy
      </button>
      <button class="fq-ctx-item" @click="copyAsTSV(true)">
        Copy with Headers
      </button>
      <button class="fq-ctx-item" @click="copyHeadersOnly()">
        Headers Only
      </button>
    </div>
  </div>
</template>

<style scoped>
.cell {
  @apply flex items-center overflow-hidden whitespace-nowrap border-b border-r border-border px-3 text-xs;
}
.cell:last-child { @apply border-r-0; }

.cell-head {
  @apply h-8 bg-surface-1 text-ink;
  font-family: Inter, system-ui, sans-serif;
}

.cell-rownum {
  @apply justify-end text-ink-subtle;
  position: sticky;
  left: 0;
  z-index: 1;
  background: rgb(var(--surface-0));
}
.cell-head.cell-rownum {
  z-index: 21;
  background: rgb(var(--surface-1));
}

.cell-rownum-click {
  cursor: pointer;
  user-select: none;
}
.cell-rownum-click:hover {
  color: rgb(var(--accent));
}

/* Column resize handle: thin, invisible until hover, on the right edge. */
.col-resize {
  position: absolute;
  top: 0;
  right: -3px;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  z-index: 22;
}
.col-resize:hover,
.col-resize:active {
  background: linear-gradient(to right, transparent 0 2px, rgb(var(--accent)) 2px 4px, transparent 4px 6px);
}
</style>
