<script setup lang="ts">
// Wraps the unified Grid with the tables view's chrome: a toolbar
// summarizing the active table, and an empty-state when nothing is
// selected.

import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTablesStore } from '../stores/tables'
import Grid from './Grid.vue'
import { useTablesGridSource } from './grid/useTablesGridSource'

const store = useTablesStore()
const { activeName, rowWindow, sort, windowLoading, sortLoading } = storeToRefs(store)
const source = useTablesGridSource()

const loadedRange = computed(() => {
  const w = rowWindow.value
  if (!w) return null
  return { start: w.absStart + 1, end: w.absStart + w.rows.length }
})
</script>

<template>
  <section class="flex min-h-0 min-w-0 flex-1 flex-col bg-surface-0">
    <div
      class="flex h-9 shrink-0 items-center gap-3 border-b border-border bg-surface-0 px-3 text-xs text-ink-muted"
    >
      <span v-if="activeName" class="font-mono text-ink">{{ activeName }}</span>
      <span v-if="store.totalRows > 0" class="font-mono tabular-nums">
        {{ store.totalRows.toLocaleString() }} rows
      </span>
      <span class="font-mono tabular-nums">{{ source.displayCols.value.length }} cols</span>
      <span
        v-if="loadedRange"
        class="font-mono tabular-nums text-ink-subtle"
        :title="`The loaded window holds rows ${loadedRange.start}–${loadedRange.end}; scrolling past it fetches the next window.`"
      >
        window {{ loadedRange.start.toLocaleString() }}–{{ loadedRange.end.toLocaleString() }}
      </span>
      <span v-if="sort" class="font-mono text-ink-subtle">
        sort · {{ sort.column }} {{ sort.dir }}
      </span>
      <span v-if="windowLoading || sortLoading" class="text-ink-subtle">
        {{ sortLoading ? 'sorting…' : 'fetching…' }}
      </span>
    </div>

    <div v-if="!activeName" class="flex flex-1 items-center justify-center">
      <div class="flex max-w-sm flex-col items-center gap-2 text-center">
        <h2 class="text-sm font-medium text-ink">No table selected</h2>
        <p class="text-xs text-ink-muted">
          Open a file, or pick a table from the sidebar.
        </p>
      </div>
    </div>

    <Grid v-else :source="source" />
  </section>
</template>
