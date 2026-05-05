// Pinia store for the free-form SQL editor.
//
// Query results materialize into a hidden workspace table on the backend
// (``__fq_sql_result_<n>``); the grid pages through it with windowed
// fetches.  No row cap.

import { markRaw } from 'vue'
import { defineStore } from 'pinia'
import {
  cancelSql,
  getRows,
  runSql,
  saveQueryAsTable,
  type SqlColumn,
  type SqlResult,
} from '../lib/api'
import { useToasts } from './toasts'
import { sortRowsByColumn } from '../lib/sortRows'
import { RESULT_WINDOW_SIZE, SORT_CAP } from '../lib/constants'

export { RESULT_WINDOW_SIZE, SORT_CAP }

type Cell = string | number | boolean | null

interface RowWindow {
  absStart: number
  rows: Cell[][]
}

interface SqlResultMeta {
  result_table: string | null
  columns: SqlColumn[]
  row_count: number
  elapsed_ms: number
}

export interface SortSpec { column: string; dir: 'asc' | 'desc' }

interface SqlState {
  text: string
  running: boolean
  lastRanSql: string | null
  meta: SqlResultMeta | null
  /** Non-null iff the last run returned an error envelope. */
  error: { message: string; elapsed_ms: number } | null
  rowWindow: RowWindow | null
  windowLoading: boolean
  sort: SortSpec | null
  sortedRows: Cell[][] | null
  sortLoading: boolean
}

export const DEFAULT_SQL = '-- Type a DuckDB query.  Ctrl+Enter or F5 to run; Esc to cancel.\n'

const STARTER_RE = /^\s*select\s+\*\s*\nfrom\s+\S+\s*\n;\s*$/i

function sqlIdent(name: string): string {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? name : '"' + name.replace(/"/g, '""') + '"'
}

/** `select * from <ident>;` formatted per our SQL conventions. */
export function starterFor(tableName: string | null): string {
  if (!tableName) return DEFAULT_SQL
  return `select *\nfrom   ${sqlIdent(tableName)}\n;\n`
}

/** True when the editor text hasn't been meaningfully edited by the user. */
export function isPristineSql(text: string): boolean {
  const trimmed = text.trim()
  if (!trimmed) return true
  if (text === DEFAULT_SQL) return true
  return STARTER_RE.test(text)
}

/** Legacy-shape accessor for any code that still expects a `SqlResult`
 *  object (StatusBar, Export dialog).  Synthesizes one from the store's
 *  current meta + window. */
function asSqlResult(state: SqlState): SqlResult | null {
  if (state.error) {
    return { ok: false, error: state.error.message, elapsed_ms: state.error.elapsed_ms }
  }
  if (!state.meta) return null
  const rows = state.rowWindow?.rows ?? []
  return {
    ok: true,
    result_table: state.meta.result_table,
    columns: state.meta.columns,
    rows,
    row_count: state.meta.row_count,
    truncated: false,
    elapsed_ms: state.meta.elapsed_ms,
  }
}

export const useSqlStore = defineStore('sql', {
  state: (): SqlState => ({
    text: DEFAULT_SQL,
    running: false,
    lastRanSql: null,
    meta: null,
    error: null,
    rowWindow: null,
    windowLoading: false,
    sort: null,
    sortedRows: null,
    sortLoading: false,
  }),

  getters: {
    /** Compatibility shim: components that used to read `result` still work. */
    result(state): SqlResult | null {
      return asSqlResult(state)
    },
    /** Row count the virtualizer should use.  Sorted mode uses the
     *  in-memory sorted array; otherwise it's the full materialized
     *  result count. */
    visibleRowCount(state): number {
      if (state.sortedRows) return state.sortedRows.length
      return state.meta?.row_count ?? 0
    },
  },

  actions: {
    setText(t: string) {
      this.text = t
    },

    async run(sql: string) {
      if (this.running) return
      this.running = true
      this.lastRanSql = sql
      this.meta = null
      this.error = null
      this.rowWindow = null
      this.sort = null
      this.sortedRows = null
      try {
        const r = await runSql(sql)
        if (r.ok) {
          this.meta = {
            result_table: r.result_table,
            columns: r.columns,
            row_count: r.row_count,
            elapsed_ms: r.elapsed_ms,
          }
          this.rowWindow = { absStart: 0, rows: markRaw(r.rows as Cell[][]) }
        } else {
          this.error = { message: r.error, elapsed_ms: r.elapsed_ms }
        }
      } catch (e) {
        this.error = {
          message: e instanceof Error ? e.message : String(e),
          elapsed_ms: 0,
        }
      } finally {
        this.running = false
      }
    },

    async toggleSort(column: string) {
      const next: SortSpec | null =
        !this.sort || this.sort.column !== column
          ? { column, dir: 'asc' }
          : this.sort.dir === 'asc'
          ? { column, dir: 'desc' }
          : null

      if (next === null) {
        this.sort = null
        this.sortedRows = null
        // Re-fetch the first window so the grid shows un-sorted rows
        // starting at row 0.
        await this.loadWindow(0)
        return
      }

      if (!this.meta?.result_table) return
      if (this.meta.row_count > SORT_CAP) {
        useToasts().show(
          `Result too large to sort (${this.meta.row_count.toLocaleString()} rows; cap is ${SORT_CAP.toLocaleString()}).`,
          'warn',
        )
        return
      }

      this.sort = next
      await this._buildSortedRows()
    },

    async _buildSortedRows() {
      if (!this.meta?.result_table || !this.sort) return
      this.sortLoading = true
      try {
        const total = this.meta.row_count
        // Reuse the first window if it already covers everything.
        let rows: Cell[][]
        if (this.rowWindow && this.rowWindow.absStart === 0 && this.rowWindow.rows.length >= total) {
          rows = this.rowWindow.rows.slice()
        } else {
          const r = await getRows(this.meta.result_table, 0, Math.min(total, SORT_CAP))
          rows = r.rows as Cell[][]
        }
        const colIdx = this.meta.columns.findIndex(c => c.name === this.sort!.column)
        if (colIdx < 0) { this.sortedRows = null; return }
        sortRowsByColumn(rows, colIdx, this.sort.dir)
        this.sortedRows = markRaw(rows)
      } catch (e) {
        this.error = { message: e instanceof Error ? e.message : String(e), elapsed_ms: 0 }
      } finally {
        this.sortLoading = false
      }
    },

    /** Fetch a windowed chunk of the result set.  Called by the grid
     *  when the viewport leaves the currently-loaded window. */
    async loadWindow(absStart: number, size: number = RESULT_WINDOW_SIZE) {
      if (!this.meta?.result_table) return
      const total = this.meta.row_count
      const clampedStart = Math.max(0, Math.min(absStart, Math.max(0, total - 1)))
      this.windowLoading = true
      try {
        const r = await getRows(this.meta.result_table, clampedStart, size)
        this.rowWindow = { absStart: r.offset, rows: markRaw(r.rows as Cell[][]) }
      } catch (e) {
        this.error = { message: e instanceof Error ? e.message : String(e), elapsed_ms: 0 }
      } finally {
        this.windowLoading = false
      }
    },

    async cancel() {
      if (!this.running) return
      await cancelSql()
    },

    async saveAsTable(tableName: string): Promise<string | null> {
      if (!this.lastRanSql) return null
      const r = await saveQueryAsTable(this.lastRanSql, tableName)
      return r.ok ? r.name : null
    },
  },
})
