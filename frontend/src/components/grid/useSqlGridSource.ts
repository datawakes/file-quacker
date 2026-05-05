// GridSource adapter for SQL query results.
//
// Carries the meta-column dedup logic that has no analog in the tables
// adapter: when a query produces both a bare `*` and an explicit
// `_src_row_num`, DuckDB suffixes the second with `_<n>`.  We keep the
// copy on whichever side the user typed the explicit reference and
// relabel the suffixed dupe back to the canonical name in the header.

import { computed, type Ref } from 'vue'
import { storeToRefs } from 'pinia'
import type { SqlColumn } from '../../lib/api'
import { RESULT_WINDOW_SIZE, useSqlStore } from '../../stores/sql'
import type { DisplayCol, GridSource } from './types'

const META_ROW_NUM = '_src_row_num'
const META_NAMES: ReadonlySet<string> = new Set([META_ROW_NUM])

function isMetaCol(name: string): boolean {
  if (META_NAMES.has(name)) return true
  const m = name.match(/^(.+)_\d+$/)
  return m !== null && META_NAMES.has(m[1])
}

// Mask string literals and comments with spaces so naive index lookups
// for `*` and `_src_row_num` ignore matches inside them.  Length is
// preserved so positions stay comparable.
function maskStringsAndComments(sql: string): string {
  let out = ''
  let i = 0
  while (i < sql.length) {
    const c = sql[i]
    if (c === "'" || c === '"') {
      out += ' '; i++
      while (i < sql.length && sql[i] !== c) { out += ' '; i++ }
      if (i < sql.length) { out += ' '; i++ }
    } else if (c === '-' && sql[i + 1] === '-') {
      while (i < sql.length && sql[i] !== '\n') { out += ' '; i++ }
    } else if (c === '/' && sql[i + 1] === '*') {
      out += '  '; i += 2
      while (i < sql.length - 1 && !(sql[i] === '*' && sql[i + 1] === '/')) { out += ' '; i++ }
      if (i < sql.length - 1) { out += '  '; i += 2 }
    } else {
      out += c; i++
    }
  }
  return out
}

export interface SqlGridSourceOpts {
  columns: Ref<SqlColumn[]>
  showMetaCols: Ref<boolean>
  onProfileColumn: (columnName: string) => void
  onPivotRow: (absIndex: number) => void
}

export function useSqlGridSource(opts: SqlGridSourceOpts): GridSource {
  const sqlStore = useSqlStore()
  const { lastRanSql, meta, rowWindow, windowLoading, sort, sortedRows, sortLoading } =
    storeToRefs(sqlStore)

  // When the SQL has both a bare `*` and an explicit `_src_row_num`, the
  // user's intent is whichever side they typed it on: returns 'last' if
  // the explicit reference comes after `*`, 'first' otherwise.
  const metaKeepWhich = computed<'first' | 'last'>(() => {
    const sql = lastRanSql.value
    if (!sql) return 'first'
    const masked = maskStringsAndComments(sql)
    const starIdx = masked.search(/(?<![\w.])\*(?!\w)/)
    const metaIdx = masked.search(/_src_row_num/i)
    return starIdx >= 0 && metaIdx > starIdx ? 'last' : 'first'
  })

  const displayCols = computed<DisplayCol[]>(() => {
    const cols = opts.columns.value
    if (!cols.length) return []
    const withIdx: DisplayCol[] = cols.map((c, i) => ({
      name: c.name,
      displayName: c.name,
      type: c.type,
      origIdx: i,
    }))
    if (opts.showMetaCols.value) {
      // Dedup keys on the meta classification, not on column name, so
      // ordinary user-named duplicates pass through untouched.  When the
      // kept copy is a DuckDB-suffixed dupe (e.g. `_src_row_num_1`),
      // relabel its header back to the canonical name.
      const metaPositions = withIdx
        .map((c, i) => isMetaCol(c.name) ? i : -1)
        .filter(i => i >= 0)
      if (metaPositions.length <= 1) return withIdx
      const keepIdx = metaKeepWhich.value === 'last'
        ? metaPositions[metaPositions.length - 1]
        : metaPositions[0]
      return withIdx
        .filter(c => !isMetaCol(c.name) || c.origIdx === keepIdx)
        .map(c => (c.origIdx === keepIdx && c.name !== META_ROW_NUM)
          ? { ...c, displayName: META_ROW_NUM }
          : c)
    }
    const withoutMeta = withIdx.filter(c => !isMetaCol(c.name))
    return withoutMeta.length ? withoutMeta : withIdx
  })

  // Stable identity for the current column set; Grid.vue resets selection
  // and scroll when this changes.
  const resetKey = computed(() =>
    opts.columns.value.map(c => c.name).join('\x1f'),
  )

  return {
    resetKey,
    displayCols,
    totalRows: computed(() => meta.value?.row_count ?? 0),
    visibleRowCount: computed(() => sqlStore.visibleRowCount),
    rowWindow,
    sortedRows,
    sort,
    windowLoading,
    sortLoading,
    windowSize: computed(() => RESULT_WINDOW_SIZE),
    loadWindow: (absStart: number) => sqlStore.loadWindow(absStart),
    toggleSort: (column: string) => sqlStore.toggleSort(column),
    jumpTo: async (absIndex: number) => {
      // Sorted mode keeps the full sorted array in memory; nothing to fetch.
      if (sortedRows.value) return
      const half = Math.floor(RESULT_WINDOW_SIZE / 2)
      const start = Math.max(0, absIndex - half)
      await sqlStore.loadWindow(start)
    },
    onProfileColumn: opts.onProfileColumn,
    onPivotRow: opts.onPivotRow,
    blurOnMouseDown: true,
    skipUnloadedRows: true,
    rowPlaceholder: 'inline',
    shouldHonorJump: (mode) => mode === 'sql',
  }
}
