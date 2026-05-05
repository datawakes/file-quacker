<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { AlertTriangle } from 'lucide-vue-next'
import { ping } from '../lib/api'
import { useTablesStore } from '../stores/tables'
import { useSqlStore } from '../stores/sql'
import { useViewStore } from '../stores/view'

const tables = useTablesStore()
const { active, activeIngest, error, loading } = storeToRefs(tables)

const sqlStore = useSqlStore()
const { result: sqlResult } = storeToRefs(sqlStore)

const viewStore = useViewStore()
const { mode } = storeToRefs(viewStore)

const duckdbVersion = ref('—')
const pingOk = ref(false)

onMounted(async () => {
  try {
    const r = await ping()
    duckdbVersion.value = r.duckdb_version
    pingOk.value = true
  } catch {
    pingOk.value = false
  }
})

function delimiterLabel(raw: string | null | undefined): string {
  if (!raw) return '—'
  if (raw === '\t') return '\\t'
  return raw
}

const firstDataRow = computed<string>(() => {
  const fr = activeIngest.value?.first_row
  if (fr === null || fr === undefined) return '—'
  return String(fr === 0 ? 1 : fr + 1)
})

// In SQL mode with a successful result, surface the result's shape
// (rows / cols / elapsed); mirrors the inline toolbar text that
// already lives above the results grid.  Grid mode falls back to the
// active table's ingest metadata.
const showSqlResult = computed(() =>
  mode.value === 'sql' && sqlResult.value && sqlResult.value.ok,
)
</script>

<template>
  <footer
    class="flex h-6 shrink-0 items-center justify-between border-t border-border px-3 text-2xs"
    style="background-color: rgb(var(--fq-yellow)); color: rgb(var(--fq-yellow-ink))"
  >
    <!-- Left: DuckDB version + transient status -->
    <div class="flex items-center gap-3 font-mono">
      <span :class="{ 'text-danger': !pingOk }">
        DuckDB <span class="opacity-70">{{ duckdbVersion }}</span>
      </span>
      <span v-if="loading" class="opacity-70">Working…</span>
      <span v-else-if="error" class="flex items-center gap-1 text-danger" :title="error">
        <AlertTriangle class="h-3 w-3" />
        <span class="truncate max-w-[500px]">{{ error.replace(/\n/g, ' · ') }}</span>
      </span>
    </div>

    <!-- Right: SQL result shape (SQL mode) OR active table shape -->
    <div class="flex items-center gap-4 font-mono tabular-nums">
      <template v-if="showSqlResult && sqlResult && sqlResult.ok">
        <span>
          <span class="opacity-70">Rows:</span>
          <span class="ml-1">{{ sqlResult.row_count.toLocaleString() }}</span>
        </span>
        <span>
          <span class="opacity-70">Cols:</span>
          <span class="ml-1">{{ sqlResult.columns.length }}</span>
        </span>
        <span>
          <span class="opacity-70">Elapsed:</span>
          <span class="ml-1">{{ sqlResult.elapsed_ms.toLocaleString() }} ms</span>
        </span>
      </template>
      <template v-else-if="active">
        <span>
          <span class="opacity-70">Rows:</span>
          <span class="ml-1">{{ active.row_count.toLocaleString() }}</span>
        </span>
        <span v-if="activeIngest">
          <span class="opacity-70">Cols:</span>
          <span class="ml-1">{{ activeIngest.col_count }}</span>
        </span>
        <span v-if="activeIngest && activeIngest.format === 'csv'">
          <span class="opacity-70">1st Data Row:</span>
          <span class="ml-1">{{ firstDataRow }}</span>
        </span>
        <span v-if="activeIngest && activeIngest.format === 'csv'">
          <span class="opacity-70">Delimiter:</span>
          <span class="ml-1">{{ delimiterLabel(activeIngest.delimiter) }}</span>
        </span>
        <span v-if="activeIngest && activeIngest.format === 'csv'">
          <span class="opacity-70">Encoding:</span>
          <span class="ml-1">{{ activeIngest.encoding || '—' }}</span>
        </span>
      </template>
      <span v-else>Ready</span>
    </div>
  </footer>
</template>
