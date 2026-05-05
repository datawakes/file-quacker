// Tunables shared across the frontend.  Keep narrow: only put a
// constant here when more than one file needs it, or when the value
// is the kind of thing a contributor will want to find quickly.

// ---- row windows / sort ------------------------------------------- //

/** Default rows per windowed fetch for the loaded-table grid. */
export const CHUNK = 5000

/** Initial preview window (renders before the full CHUNK lands). */
export const INITIAL_PREVIEW_ROWS = 100

/** First window of a SQL result; the grid pages further on demand. */
export const RESULT_WINDOW_SIZE = 500

/** Max rows held in memory for client-side sort; bigger surfaces a toast. */
export const SORT_CAP = 100_000

// ---- grid layout -------------------------------------------------- //

/** Row height in pixels - feeds the virtualizer's estimateSize. */
export const ROW_H = 28

/** Approximate character width in the grid font (Inter 12px tabular).
 *  Used for auto-sizing column widths from sample content. */
export const CHAR_W = 7.2

/** Min / max auto-fit column widths. */
export const COL_MIN = 72
export const COL_MAX = 300

/** Cell horizontal padding budget (left + right). */
export const CELL_PAD = 24

/** Min width of the row-number gutter. */
export const ROWNUM_W_MIN = 56

/** Rows sampled from the loaded window for column auto-sizing. */
export const SAMPLE_ROWS = 200

/** Header reserves room for the sort caret + profile icon. */
export const HEADER_ICON_BUDGET = 24

/** Debounce on viewport-leaves-window before re-fetching. */
export const FETCH_DEBOUNCE_MS = 120
