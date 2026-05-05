<script setup lang="ts">
// Multi-file Parquet chooser.  Shown after the user opens a .parquet
// file that has sibling .parquet files in the same directory.
// Two outcomes:
//   - Just this file: load only the file the user picked.
//   - Load all N: union every .parquet in the folder via DuckDB's
//     `read_parquet([...])`.

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { File, Files, X } from 'lucide-vue-next'
import { useViewStore } from '../stores/view'
import { useTablesStore } from '../stores/tables'
import { useFocusTrap } from '../composables/useFocusTrap'

const viewStore = useViewStore()
const tables = useTablesStore()
const { parquetSet } = storeToRefs(viewStore)

const dialogRef = ref<HTMLElement | null>(null)
useFocusTrap(dialogRef)

function close() { viewStore.closeParquetSet() }

function onKey(e: KeyboardEvent) { if (e.key === 'Escape') close() }
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

const peers = computed(() => parquetSet.value?.peers ?? [])
const totalBytes = computed(() => parquetSet.value?.total_bytes ?? 0)
const thisPath = computed(() => parquetSet.value?.this_path ?? '')

function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path
}
function fmtBytes(n: number): string {
  if (n < 1024)             return `${n} B`
  if (n < 1024 * 1024)      return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3)        return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

async function loadJustThis() {
  const p = thisPath.value
  close()
  if (p) await tables.loadPath(p)
}

async function loadAll() {
  const set = parquetSet.value
  if (!set) return close()
  close()
  await tables.loadPath(set.this_path, { parquet_paths: set.peers })
}
</script>

<template>
  <div
    v-if="parquetSet"
    class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-[1px]"
  >
    <div
      ref="dialogRef"
      class="flex flex-col rounded-md border border-border bg-surface-1 shadow-xl"
      style="height: min(560px, 84vh); width: min(640px, 92vw);"
      role="dialog"
      aria-modal="true"
      aria-labelledby="parquet-set-title"
    >
      <!-- Header -->
      <div class="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <Files class="h-4 w-4 text-ink-muted" />
        <h2 id="parquet-set-title" class="text-sm font-medium text-ink">
          Multi-file Parquet dataset
        </h2>
        <button type="button" class="btn-icon ml-auto" aria-label="Close" title="Close" @click="close">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- Body -->
      <div class="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-xs text-ink-secondary">
        <p class="mb-3">
          The folder for the file you opened contains
          <strong>{{ peers.length }} Parquet files</strong>
          ({{ fmtBytes(totalBytes) }} total). They typically belong to
          the same dataset (Spark / Hive output) and should be loaded
          together so the row count matches the source.
        </p>
        <div class="rounded border border-border bg-surface-0">
          <ul class="divide-y divide-border max-h-72 overflow-y-auto font-mono text-2xs">
            <li
              v-for="p in peers"
              :key="p"
              class="flex items-center gap-2 px-3 py-1.5"
              :class="{ 'bg-accent/10 text-ink': p === thisPath }"
            >
              <File class="h-3 w-3 shrink-0 text-ink-subtle" />
              <span class="truncate" :title="p">{{ basename(p) }}</span>
            </li>
          </ul>
        </div>
        <p class="mt-3 text-2xs text-ink-subtle">
          The highlighted file is the one you just selected.
        </p>
      </div>

      <!-- Footer -->
      <div class="flex h-12 shrink-0 items-center justify-end gap-2 border-t border-border px-3">
        <button type="button" class="btn-secondary" @click="close">Cancel</button>
        <button type="button" class="btn-secondary" @click="loadJustThis">
          Just this file
        </button>
        <button type="button" class="btn-primary" @click="loadAll">
          Load all {{ peers.length }} files
        </button>
      </div>
    </div>
  </div>
</template>
