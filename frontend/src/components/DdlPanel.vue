<script setup lang="ts">
// CREATE TABLE DDL for the selected table; DuckDB or SQL Server dialect.
// Text is selectable for copy/paste; a Copy button writes the whole DDL
// to the clipboard in one shot.

import { ref, watch } from 'vue'
import { Copy, X } from 'lucide-vue-next'
import { generateDdl } from '../lib/api'
import { useToasts } from '../stores/toasts'

const props = defineProps<{ table: string }>()
defineEmits<{ (e: 'close'): void }>()

type Dialect = 'duckdb' | 'sqlserver'

const toasts = useToasts()
const dialect = ref<Dialect>('sqlserver')
const sql = ref<string>('')
const loading = ref(false)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const r = await generateDdl(props.table, dialect.value)
    sql.value = r.sql
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

watch(() => [props.table, dialect.value] as const, load, { immediate: true })

async function onCopy() {
  try {
    await navigator.clipboard.writeText(sql.value)
    toasts.show('DDL copied to clipboard', 'success', 2000)
  } catch {
    toasts.show('Copy failed', 'error')
  }
}

/** Strip HTML styling on manual copy; only plain text lands on the clipboard. */
function onPanelCopy(e: ClipboardEvent) {
  const sel = window.getSelection()
  if (!sel || !sel.toString()) return
  e.preventDefault()
  e.clipboardData?.setData('text/plain', sel.toString())
}
</script>

<template>
  <aside class="flex h-full flex-col bg-surface-0" aria-label="DDL" @copy="onPanelCopy">
    <!-- Header -->
    <div class="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
      <div class="flex min-w-0 flex-1 items-baseline gap-2">
        <span class="text-sm text-ink">DDL</span>
        <span class="truncate font-mono text-2xs text-ink-subtle" :title="table">{{ table }}</span>
      </div>
      <button class="btn-icon" title="Copy DDL" aria-label="Copy DDL" @click="onCopy">
        <Copy class="h-4 w-4" />
      </button>
      <button class="btn-icon" title="Close" aria-label="Close" @click="$emit('close')">
        <X class="h-4 w-4" />
      </button>
    </div>

    <!-- Dialect tabs -->
    <div class="flex h-8 shrink-0 items-center gap-0.5 border-b border-border bg-surface-0 px-2">
      <button
        type="button"
        class="mode-btn"
        :class="{ 'mode-btn-on': dialect === 'sqlserver' }"
        @click="dialect = 'sqlserver'"
      >SQL Server</button>
      <button
        type="button"
        class="mode-btn"
        :class="{ 'mode-btn-on': dialect === 'duckdb' }"
        @click="dialect = 'duckdb'"
      >DuckDB</button>
    </div>

    <!-- Body -->
    <div class="min-h-0 flex-1 overflow-auto px-3 py-3">
      <div v-if="loading" class="text-xs text-ink-subtle">Generating…</div>
      <div v-else-if="error" class="font-mono text-xs text-danger whitespace-pre-wrap">{{ error }}</div>
      <pre
        v-else
        class="m-0 select-text whitespace-pre font-mono text-xs leading-5 text-ink"
      >{{ sql }}</pre>
    </div>
  </aside>
</template>

<style scoped>
.mode-btn {
  @apply inline-flex items-center rounded px-2 py-0.5 font-medium text-xs text-ink-muted transition-colors hover:text-ink;
}
.mode-btn-on {
  @apply bg-surface-2 text-ink;
}
</style>
