<script setup lang="ts">
// Wraps the unified Grid for SQL results: empty-state when no result yet,
// floating fetching/sorting badge bottom-right, and adapters for the
// pivot / profile actions which the SQL editor handles via emits.

import { computed, toRef } from 'vue'
import { storeToRefs } from 'pinia'
import type { SqlColumn } from '../lib/api'
import { useSqlStore } from '../stores/sql'
import Grid from './Grid.vue'
import { useSqlGridSource } from './grid/useSqlGridSource'

const props = defineProps<{
  columns: SqlColumn[]
  /** True when the enclosing caller explicitly references `_src_row_num`
   *  in the query; shows the meta column in the grid. */
  showMetaCols?: boolean
}>()

const emit = defineEmits<{
  (e: 'profile-column', columnName: string): void
  (e: 'pivot-row', rowIndex: number): void
}>()

const sqlStore = useSqlStore()
const { windowLoading, sortLoading } = storeToRefs(sqlStore)

const source = useSqlGridSource({
  columns: toRef(props, 'columns'),
  showMetaCols: computed(() => props.showMetaCols ?? false),
  onProfileColumn: (col: string) => emit('profile-column', col),
  onPivotRow: (idx: number) => emit('pivot-row', idx),
})
</script>

<template>
  <div class="flex min-h-0 min-w-0 flex-1 flex-col">
    <div
      v-if="columns.length === 0"
      class="flex flex-1 items-center justify-center text-xs text-ink-subtle"
    >
      No result yet — run a query.
    </div>
    <Grid v-else :source="source" />

    <div
      v-if="windowLoading || sortLoading"
      class="pointer-events-none absolute bottom-4 right-6 rounded-md bg-surface-1/90 px-2 py-1 text-2xs text-ink-subtle shadow"
    >
      {{ sortLoading ? 'sorting…' : 'fetching…' }}
    </div>
  </div>
</template>
