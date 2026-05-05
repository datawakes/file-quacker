// Which primary view is active in the main pane, and which right-hand
// inspection panel (if any) is open.  Only one right-pane panel is
// visible at a time; opening profile / pivot / ddl closes the others.

import { defineStore } from 'pinia'
import type { ExcelFileInfo, ExportSource, ParquetSetInfo } from '../lib/api'

export type ViewMode = 'grid' | 'sql'

export interface TableSource  { kind: 'table'; name: string }
export interface QuerySource  { kind: 'query'; sql: string  }
export type ProfileSource = TableSource | QuerySource

export interface ProfileTarget {
  source: ProfileSource
  column: string
}

/** Snapshot of a single row for the pivot panel; values captured at
 *  click time so the panel doesn't get stale if the row window shifts. */
export interface PivotTarget {
  tableLabel: string
  rowNum: number
  columns: { name: string; type: string }[]
  values: (string | number | boolean | null)[]
}

export interface DdlTarget {
  table: string
}

export interface IngestErrorTarget {
  path: string
  fileName: string
  message: string
}

/** Target for the Open-with-options / Reload-with-options dialog.
 *  When `reloadTable` is set, the dialog drops that workspace table
 *  before re-loading and reuses the same name. */
export interface IngestOptionsTarget {
  path: string
  reloadTable?: string | null
}

export const useViewStore = defineStore('view', {
  state: () => ({
    mode: 'grid' as ViewMode,
    profile:      null as ProfileTarget      | null,
    pivot:        null as PivotTarget        | null,
    ddl:          null as DdlTarget          | null,
    ingestError:  null as IngestErrorTarget  | null,
    exportSource: null as ExportSource       | null,
    /** When non-null, show the multi-file Parquet chooser modal. */
    parquetSet:   null as ParquetSetInfo     | null,
    /** When non-null, show the Excel sheet chooser modal. */
    excelFile:    null as ExcelFileInfo      | null,
    /** Toggle the Import-from-DB dialog. */
    importDb:     false,
    /** Open-with-options / Reload-with-options dialog target. */
    ingestOptions: null as IngestOptionsTarget | null,
    // Jump-to-row plumbing.  TopBar submits a 1-based row number; the
    // currently-mounted grid watches `jumpTick` and acts.  The tick is
    // monotonic so the same row can be jumped to twice in a row.
    pendingJumpRow: null as number | null,
    jumpTick: 0,
  }),

  getters: {
    rightPanelOpen: (state) =>
      state.profile !== null
      || state.pivot !== null
      || state.ddl !== null
      || state.ingestError !== null,
  },

  actions: {
    setMode(m: ViewMode) { this.mode = m },

    profileTable(name: string, column: string) {
      this.profile = { source: { kind: 'table', name }, column }
      this.pivot = null
      this.ddl = null
      this.ingestError = null
    },
    profileQuery(sql: string, column: string) {
      this.profile = { source: { kind: 'query', sql }, column }
      this.pivot = null
      this.ddl = null
      this.ingestError = null
    },
    openPivot(target: PivotTarget) {
      this.pivot = target
      this.profile = null
      this.ddl = null
      this.ingestError = null
    },
    openDdl(table: string) {
      this.ddl = { table }
      this.profile = null
      this.pivot = null
      this.ingestError = null
    },
    openIngestError(target: IngestErrorTarget) {
      this.ingestError = target
      this.profile = null
      this.pivot = null
      this.ddl = null
    },
    openExport(src: ExportSource) { this.exportSource = src },
    closeExport() { this.exportSource = null },
    openParquetSet(info: ParquetSetInfo) { this.parquetSet = info },
    closeParquetSet() { this.parquetSet = null },
    openExcelFile(info: ExcelFileInfo) { this.excelFile = info },
    closeExcelFile() { this.excelFile = null },
    openImportDb() { this.importDb = true },
    closeImportDb() { this.importDb = false },
    openIngestOptions(target: IngestOptionsTarget) { this.ingestOptions = target },
    closeIngestOptions() { this.ingestOptions = null },
    closeProfile() { this.profile = null },
    closeRight() {
      this.profile = null
      this.pivot = null
      this.ddl = null
      this.ingestError = null
    },
    jumpToRow(n: number) {
      this.pendingJumpRow = n
      this.jumpTick = this.jumpTick + 1
    },
  },
})
