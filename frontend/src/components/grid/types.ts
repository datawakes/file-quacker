// Single contract for the unified Grid.vue.  Each backing store gets a
// thin adapter (useTablesGridSource / useSqlGridSource) that maps onto
// this shape, so the grid component itself is store-agnostic.

import type { ComputedRef, Ref } from 'vue'

export type Cell = string | number | boolean | null

export interface DisplayCol {
  /** Real column name; used for sort and profile API calls. */
  name: string
  /** Header label + TSV header column.  Equal to `name` for tables;
   *  differs only when the SQL adapter relabels a kept duplicate of the
   *  meta column back to its canonical name. */
  displayName: string
  type: string
  /** Index into the raw row array (the source `columns[]` order). */
  origIdx: number
}

export interface RowWindow {
  absStart: number
  rows: Cell[][]
}

export interface SortSpec { column: string; dir: 'asc' | 'desc' }

export type ViewMode = 'grid' | 'sql'

export interface GridSource {
  /** Identity used to reset selection, widths, and scroll position. */
  resetKey: ComputedRef<string | null>

  displayCols: ComputedRef<DisplayCol[]>
  totalRows: ComputedRef<number>
  visibleRowCount: ComputedRef<number>
  rowWindow: Ref<RowWindow | null>
  sortedRows: Ref<Cell[][] | null>
  sort: Ref<SortSpec | null>
  windowLoading: Ref<boolean>
  sortLoading: Ref<boolean>

  /** Window size used when computing the centered fetch target. */
  windowSize: ComputedRef<number>

  loadWindow(absStart: number): Promise<void> | void
  toggleSort(columnName: string): Promise<void> | void
  /** Pre-fetch any window needed to make `absIndex` visible.  Caller
   *  scrolls after this resolves. */
  jumpTo(absIndex: number): Promise<void>
  onProfileColumn(columnName: string): void
  onPivotRow(absIndex: number): void

  /** Blur active editor before mouse handlers (CodeMirror keeps
   *  contenteditable focus, breaking the global Ctrl+C path). */
  blurOnMouseDown: boolean
  /** Reject selection / context menu on rows whose data hasn't loaded. */
  skipUnloadedRows: boolean
  /** Whole-row placeholder block vs. per-cell dim style with `loading…`. */
  rowPlaceholder: 'block' | 'inline'

  /** Whether to honor `viewStore.jumpTick` in the current view mode. */
  shouldHonorJump(viewMode: ViewMode): boolean
}
