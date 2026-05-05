// GridSource adapter for the loaded-tables view.

import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTablesStore } from '../../stores/tables'
import { useViewStore } from '../../stores/view'
import type { Cell, DisplayCol, GridSource } from './types'

const META_ROW_NUM = '_src_row_num'

export function useTablesGridSource(): GridSource {
  const store = useTablesStore()
  const viewStore = useViewStore()
  const { activeName, schema, rowWindow, windowLoading, sort, sortedRows, sortLoading } =
    storeToRefs(store)

  function rowAt(absIndex: number): Cell[] | null {
    if (sortedRows.value) return sortedRows.value[absIndex] ?? null
    const w = rowWindow.value
    if (!w) return null
    const local = absIndex - w.absStart
    return (local >= 0 && local < w.rows.length) ? w.rows[local] : null
  }

  // `_src_row_num` is internal metadata; surface it only when the schema
  // contains nothing else, so the grid isn't empty.
  const displayCols = computed<DisplayCol[]>(() => {
    const cols = schema.value
    if (!cols.length) return []
    const withIdx = cols.map((c, i) => ({
      name: c.name,
      displayName: c.name,
      type: c.type,
      origIdx: i,
    }))
    const withoutMeta = withIdx.filter(c => c.name !== META_ROW_NUM)
    return withoutMeta.length ? withoutMeta : withIdx
  })

  return {
    resetKey: computed(() => activeName.value),
    displayCols,
    totalRows: computed(() => store.totalRows),
    visibleRowCount: computed(() => store.visibleRowCount),
    rowWindow,
    sortedRows,
    sort,
    windowLoading,
    sortLoading,
    windowSize: computed(() => store.effectiveChunkSize),
    loadWindow: (absStart: number) => store.loadWindow(absStart),
    toggleSort: (column: string) => store.toggleSort(column),
    jumpTo: (idx: number) => store.jumpTo(idx),
    onProfileColumn: (col: string) => {
      if (activeName.value) viewStore.profileTable(activeName.value, col)
    },
    onPivotRow: (absIndex: number) => {
      if (!activeName.value) return
      const row = rowAt(absIndex)
      if (!row) return
      const keep = schema.value
        .map((c, i) => ({ c, i }))
        .filter(x => x.c.name !== META_ROW_NUM)
      viewStore.openPivot({
        tableLabel: activeName.value,
        rowNum: absIndex + 1,
        columns: keep.map(x => ({ name: x.c.name, type: x.c.type })),
        values: keep.map(x => row[x.i]),
      })
    },
    blurOnMouseDown: false,
    skipUnloadedRows: false,
    rowPlaceholder: 'block',
    shouldHonorJump: (mode) => mode === 'grid',
  }
}
