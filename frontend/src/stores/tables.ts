// Pinia store for the workspace: loaded tables, the active selection,
// a sliding row window, and the client-side sort.
//
// Two modes for row indexing:
//   * Unsorted: virtualizer indices == absolute table row.  A sliding
//     CHUNK-row window sits in memory; `loadWindow` swaps in a new
//     window when the viewport leaves the loaded range.
//   * Sorted: load up to SORT_CAP rows, sort in JS, feed the
//     virtualizer from that array.  Tables over SORT_CAP refuse to
//     sort and toast a warning.

import { markRaw } from 'vue'
import { defineStore } from 'pinia'
import { useViewStore } from './view'
import {
  dropTable,
  getRows,
  getTableSchema,
  inspectExcelFile,
  inspectParquetSet,
  listTables,
  loadExcelWorkbook,
  loadFile,
  materializeTable,
  openFileDialog,
  renameTable,
  type ColumnMeta,
  type ExcelSheetSpec,
  type IngestOptions,
  type IngestResult,
  type SortSpec,
  type TableInfo,
} from '../lib/api'

export type SchemaByTable = Record<string, ColumnMeta[]>
import { useToasts } from './toasts'
import { sortRowsByColumn } from '../lib/sortRows'
import { CHUNK, INITIAL_PREVIEW_ROWS, SORT_CAP } from '../lib/constants'

export { CHUNK, SORT_CAP }

// Smaller windows on wide tables so scroll-fetches stay snappy.
function chunkForColCount(n: number): number {
  if (n > 100) return 1000
  if (n > 50)  return 2000
  return CHUNK
}

type Cell = string | number | boolean | null

interface RowWindow {
  absStart: number
  rows: Cell[][]
}

/** Summary of how a table was loaded; source file, delimiter, encoding,
 *  header row.  Shown in the status bar; persisted only for the session. */
export interface IngestSummary {
  source_path: string
  format: string
  col_count: number
  delimiter: string | null
  encoding: string | null
  first_row: number | null
  has_rejects: boolean
}

interface TablesState {
  tables: TableInfo[]
  activeName: string | null
  schema: ColumnMeta[]
  rowWindow: RowWindow | null
  sort: SortSpec | null
  sortedRows: Cell[][] | null
  loading: boolean
  windowLoading: boolean
  sortLoading: boolean
  error: string | null
  /** Column metadata cached per table name; populated on sidebar expand. */
  schemaByTable: SchemaByTable
  expandedTables: Record<string, boolean>
  /** Ingest summary keyed by table name; populated when load_file returns. */
  ingestInfo: Record<string, IngestSummary>
}

export const useTablesStore = defineStore('tables', {
  state: (): TablesState => ({
    tables: [],
    activeName: null,
    schema: [],
    rowWindow: null,
    sort: null,
    sortedRows: null,
    loading: false,
    windowLoading: false,
    sortLoading: false,
    error: null,
    schemaByTable: {},
    expandedTables: {},
    ingestInfo: {},
  }),

  getters: {
    active(state): TableInfo | null {
      return state.tables.find(t => t.name === state.activeName) ?? null
    },
    totalRows(): number {
      return this.active?.row_count ?? 0
    },
    /** The row count the grid virtualizer should use. */
    visibleRowCount(): number {
      return this.sortedRows ? this.sortedRows.length : this.totalRows
    },
    activeIngest(state): IngestSummary | null {
      return state.activeName ? (state.ingestInfo[state.activeName] ?? null) : null
    },
    effectiveChunkSize(state): number {
      return chunkForColCount(state.schema.length)
    },
  },

  actions: {
    async refresh() {
      this.tables = await listTables()
      if (this.activeName && !this.tables.some(t => t.name === this.activeName)) {
        this.activeName = null
        this.schema = []
        this.rowWindow = null
        this.sort = null
        this.sortedRows = null
      }
    },

    async selectTable(name: string) {
      this.activeName = name
      this.sort = null
      this.sortedRows = null
      this.loading = true
      this.error = null
      try {
        this.schema = await getTableSchema(name)
        // Quick preview so columns size and the visible rows render;
        // the full window streams in below.
        await this.loadWindow(0, INITIAL_PREVIEW_ROWS)
      } catch (e) {
        this.error = e instanceof Error ? e.message : String(e)
        this.loading = false
        return
      }
      this.loading = false
      if (this.totalRows > INITIAL_PREVIEW_ROWS) {
        void this.loadWindow(0, this.effectiveChunkSize)
      }
    },

    async loadWindow(absStart: number, size?: number) {
      if (!this.activeName) return
      const effective = size ?? this.effectiveChunkSize
      const total = this.totalRows
      const clampedStart = Math.max(0, Math.min(absStart, Math.max(0, total - 1)))
      this.windowLoading = true
      try {
        const r = await getRows(this.activeName, clampedStart, effective)
        // Rows are write-once; markRaw skips Vue's deep-proxy overhead.
        this.rowWindow = { absStart: r.offset, rows: markRaw(r.rows as Cell[][]) }
      } catch (e) {
        this.error = e instanceof Error ? e.message : String(e)
      } finally {
        this.windowLoading = false
      }
    },

    async jumpTo(absIndex: number) {
      // Jump-to-row is scroll-by-absolute; sorted mode reorders so the
      // index doesn't mean the same thing.  No-op there.
      if (this.sortedRows) return
      const total = this.totalRows
      if (total === 0) return
      const clamped = Math.max(0, Math.min(absIndex, total - 1))
      const size = this.effectiveChunkSize
      const half = Math.floor(size / 2)
      const start = Math.max(0, Math.min(clamped - half, Math.max(0, total - size)))
      await this.loadWindow(start)
    },

    /**
     * Click-to-sort cycles: asc → desc → cleared.  Sorting loads up to
     * SORT_CAP rows and sorts them client-side.  If the table is bigger,
     * a toast fires and the sort state is left unchanged.
     */
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
        await this.loadWindow(0)
        return
      }

      if (this.totalRows > SORT_CAP) {
        useToasts().show(
          `Table too large to sort (${this.totalRows.toLocaleString()} rows; cap is ${SORT_CAP.toLocaleString()}).`,
          'warn',
        )
        return
      }

      this.sort = next
      await this._buildSortedRows()
    },

    async _buildSortedRows() {
      if (!this.activeName || !this.sort) return
      this.sortLoading = true
      try {
        // Reuse the window if it already covers everything, else fetch all.
        const total = this.totalRows
        let rows: Cell[][]
        if (this.rowWindow && this.rowWindow.absStart === 0 && this.rowWindow.rows.length >= total) {
          rows = this.rowWindow.rows.slice()
        } else {
          const r = await getRows(this.activeName, 0, Math.min(total, SORT_CAP))
          rows = r.rows as Cell[][]
        }

        const colIdx = this.schema.findIndex(c => c.name === this.sort!.column)
        if (colIdx < 0) {
          this.sortedRows = null
          return
        }
        sortRowsByColumn(rows, colIdx, this.sort.dir)
        this.sortedRows = markRaw(rows)
      } catch (e) {
        this.error = e instanceof Error ? e.message : String(e)
      } finally {
        this.sortLoading = false
      }
    },

    async openFile() {
      const path = await openFileDialog()
      if (!path) return null
      return this.openPath(path)
    },

    /** Entry point for any user-initiated file open.  Detects multi-
     *  file Parquet datasets, multi-sheet Excel workbooks, and Excel
     *  files with preamble blocks; defers to the appropriate chooser
     *  modal in those cases.  Otherwise loads via ``loadPath``.
     *
     *  ``advanced`` routes plain-text files (and Excel-named-text
     *  ELT exports) to the IngestOptionsDialog so the user can pick
     *  the header row / delimiter / encoding before ingest.  Real
     *  Excel and multi-file Parquet still go through their dedicated
     *  chooser modals; those *are* the "advanced" path for those
     *  formats. */
    async openPath(path: string, opts?: { advanced?: boolean }) {
      const advanced = !!opts?.advanced
      const lower = path.toLowerCase()
      if (lower.endsWith('.parquet')) {
        try {
          const info = await inspectParquetSet(path)
          if (info.peers.length > 1) {
            useViewStore().openParquetSet(info)
            return null
          }
        } catch (e) {
          console.warn('[tables] inspect_parquet_set failed:', e)
        }
      } else if (lower.endsWith('.xlsx') || lower.endsWith('.xlsm') || lower.endsWith('.xls')) {
        try {
          const info = await inspectExcelFile(path)
          if (info.kind === 'text') {
            // ELT-transformed pipe-delimited file masquerading as
            // Excel; fall through to the text path below.
            if (advanced) {
              useViewStore().openIngestOptions({ path })
              return null
            }
            return this.loadPath(path)
          }
          // Show the chooser when there's more than one sheet, any
          // sheet has an auto-detected preamble, OR the user
          // explicitly asked for advanced options.
          const sheets = info.sheets || []
          const needsChooser = advanced || sheets.length > 1 || sheets.some(s => s.detected)
          if (needsChooser) {
            useViewStore().openExcelFile(info)
            return null
          }
        } catch (e) {
          console.warn('[tables] inspect_excel_file failed:', e)
        }
      } else if (advanced) {
        // Plain-text: route to the IngestOptionsDialog.
        useViewStore().openIngestOptions({ path })
        return null
      }
      return this.loadPath(path)
    },

    /** Multi-sheet Excel ingest.  Calls the backend once and refreshes
     *  the sidebar to surface every created table; selects the first
     *  one in the grid.  Used from the Excel sheet chooser. */
    async loadExcelSheets(path: string, specs: ExcelSheetSpec[], opts?: IngestOptions) {
      this.loading = true
      this.error = null
      const toasts = useToasts()
      const fileName = path.split(/[\\/]/).pop() || path
      const label = specs.length === 1 ? `sheet ${specs[0].name}` : `${specs.length} sheets`
      const toastId = toasts.showSticky(`Loading ${label} from ${fileName}…`, 'info')
      try {
        const results = await loadExcelWorkbook(path, specs, opts)
        for (const r of results) this._recordIngest(r)
        await this.refresh()
        if (results.length) await this.selectTable(results[0].name)
        toasts.dismiss(toastId)
        const totalRows = results.reduce((n, r) => n + (r.row_count || 0), 0)
        const summary = results.length === 1
          ? `Loaded sheet ${results[0].name} — ${results[0].row_count.toLocaleString()} rows`
          : `Loaded ${results.length} sheets from ${fileName} — ${totalRows.toLocaleString()} rows total`
        toasts.show(summary, 'success')
        return results
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        this.error = msg
        toasts.dismiss(toastId)
        useViewStore().openIngestError({ path, fileName, message: msg })
        throw e
      } finally {
        this.loading = false
      }
    },

    async loadPath(path: string, opts?: IngestOptions) {
      this.loading = true
      this.error = null
      const toasts = useToasts()
      const fileName = path.split(/[\\/]/).pop() || path
      const toastId = toasts.showSticky(`Loading ${fileName}…`, 'info')
      try {
        const r: IngestResult = await loadFile(path, opts)
        this._recordIngest(r)
        await this.refresh()
        await this.selectTable(r.name)
        toasts.dismiss(toastId)
        toasts.show(`Loaded ${fileName} — ${r.row_count.toLocaleString()} rows`, 'success')
        return r
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        this.error = msg
        toasts.dismiss(toastId)
        useViewStore().openIngestError({ path, fileName, message: msg })
        throw e
      } finally {
        this.loading = false
      }
    },

    /** Fetch the column list for every workspace table that doesn't
     *  already have one cached.  Used by the SQL editor to feed
     *  lang-sql's autocomplete schema so `alias.` suggestions work
     *  without the user having to expand each table in the sidebar. */
    async ensureAllSchemas() {
      const missing = this.tables
        .map(t => t.name)
        .filter(n => !this.schemaByTable[n])
      if (!missing.length) return
      const next: SchemaByTable = { ...this.schemaByTable }
      await Promise.all(missing.map(async (name) => {
        try { next[name] = await getTableSchema(name) } catch { /* ignore */ }
      }))
      this.schemaByTable = next
    },

    async toggleExpand(name: string) {
      const open = !this.expandedTables[name]
      this.expandedTables = { ...this.expandedTables, [name]: open }
      if (open && !this.schemaByTable[name]) {
        try {
          const s = await getTableSchema(name)
          this.schemaByTable = { ...this.schemaByTable, [name]: s }
        } catch {
          this.expandedTables = { ...this.expandedTables, [name]: false }
        }
      }
    },

    async renameActive(newName: string) {
      if (!this.activeName) return
      await renameTable(this.activeName, newName)
      this.activeName = newName
      await this.refresh()
    },

    async dropByName(name: string) {
      await dropTable(name)
      await this.refresh()
    },

    async materializeActive() {
      if (!this.activeName) return
      await materializeTable(this.activeName)
      await this.refresh()
    },

    _recordIngest(r: IngestResult) {
      this.ingestInfo = {
        ...this.ingestInfo,
        [r.name]: {
          source_path: r.source_path,
          format: r.format,
          col_count: r.col_count,
          delimiter: r.delimiter,
          encoding: r.encoding,
          first_row: r.first_row,
          has_rejects: r.has_rejects,
        },
      }
    },
  },
})
