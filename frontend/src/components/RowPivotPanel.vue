<script setup lang="ts">
// Pivot a single row so every column becomes its own row.  Useful for
// copying the row's values into a spreadsheet for analysis.  Text is
// selectable; a Copy All button writes a TSV representation.

import { Copy, X } from 'lucide-vue-next'
import type { PivotTarget } from '../stores/view'
import { useToasts } from '../stores/toasts'

const props = defineProps<{ target: PivotTarget }>()
defineEmits<{ (e: 'close'): void }>()

const toasts = useToasts()

function fmtCell(v: unknown): { text: string; isNull: boolean } {
  if (v === null || v === undefined) return { text: 'NULL', isNull: true }
  return { text: String(v), isNull: false }
}

async function onCopyTsv() {
  const t = props.target
  const lines = ['col_num\tcolumn\tvalue']
  t.columns.forEach((col, i) => {
    const v = fmtCell(t.values[i])
    lines.push(`${i + 1}\t${col.name}\t${v.isNull ? '' : v.text}`)
  })
  try {
    await navigator.clipboard.writeText(lines.join('\n'))
    toasts.show('Copied as TSV — paste into Excel', 'success', 2500)
  } catch {
    toasts.show('Copy failed', 'error')
  }
}

/** Intercept native copy; write only `text/plain` so Excel paste
 *  doesn't pick up our theme's font / background via the HTML flavor. */
function onPanelCopy(e: ClipboardEvent) {
  const sel = window.getSelection()
  if (!sel || !sel.toString()) return
  e.preventDefault()
  e.clipboardData?.setData('text/plain', sel.toString())
}
</script>

<template>
  <aside class="flex h-full flex-col bg-surface-0" aria-label="Row pivot" @copy="onPanelCopy">
    <div class="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
      <div class="flex min-w-0 flex-1 items-baseline gap-2">
        <span class="text-sm text-ink">Row</span>
        <span class="font-mono text-sm tabular-nums text-ink">{{ target.rowNum.toLocaleString() }}</span>
        <span class="truncate font-mono text-2xs text-ink-subtle" :title="target.tableLabel">{{ target.tableLabel }}</span>
      </div>
      <button class="btn-icon" title="Copy as TSV" aria-label="Copy as TSV" @click="onCopyTsv">
        <Copy class="h-4 w-4" />
      </button>
      <button class="btn-icon" title="Close" aria-label="Close" @click="$emit('close')">
        <X class="h-4 w-4" />
      </button>
    </div>

    <div class="min-h-0 flex-1 overflow-auto px-3 py-3 text-xs">
      <table class="w-full select-text border-collapse">
        <thead class="sticky top-0 bg-surface-0 text-2xs uppercase tracking-wide text-ink-subtle">
          <tr class="text-left">
            <th class="py-1.5 pr-3 font-medium tabular-nums">#</th>
            <th class="py-1.5 pr-3 font-medium">Column</th>
            <th class="py-1.5 pr-3 font-medium">Value</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(col, i) in target.columns"
            :key="col.name"
            class="border-t border-border/70 align-top"
          >
            <td class="py-1 pr-3 font-mono tabular-nums text-ink-subtle">{{ i + 1 }}</td>
            <td class="py-1 pr-3 font-mono text-ink" :title="col.type">{{ col.name }}</td>
            <td class="py-1 pr-3">
              <span
                v-if="fmtCell(target.values[i]).isNull"
                class="italic text-ink-subtle"
              >NULL</span>
              <span
                v-else
                class="whitespace-pre-wrap break-words font-mono text-ink"
              >{{ fmtCell(target.values[i]).text }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </aside>
</template>
