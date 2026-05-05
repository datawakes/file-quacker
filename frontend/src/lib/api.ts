// Typed bridge to the Python backend exposed by pywebview.
//
// Every call goes through `withApi()`; it waits for the `pywebviewready`
// event the first time, caches the resolved reference, and rejects after a
// timeout if we're running in a plain browser (no pywebview shell).

export interface PingResponse {
  ok: boolean
  message: string
  app_version: string
  python_version: string
  platform: string
  duckdb_version: string
}

export interface ColumnMeta {
  name: string
  type: string
  nullable?: boolean
}

export interface TableInfo {
  name: string
  kind: 'table' | 'view'
  row_count: number
}

export interface IngestOptions {
  delimiter?: string | null
  quote?: string
  escape?: string | null
  encoding?: string | null
  first_row?: number
  skip_rows?: number
  ignore_errors?: boolean
  materialize?: boolean
  sheet?: string | null
  preferred_table_name?: string | null
  /** Multi-file Parquet ingest.  When present and length > 1, the
   *  loader unions every listed path via DuckDB's `read_parquet([...])`. */
  parquet_paths?: string[] | null
  /** Excel preamble cropping (1-based row, A-style column letters). */
  excel_header_row?: number | null
  excel_start_col?: string | null
  excel_end_col?: string | null
}

export interface ExcelSheetInfo {
  name: string
  header_row: number
  start_col: string
  end_col: string
  /** True when our auto-detect chose anything other than A1. */
  detected: boolean
  /** First ~30 rows × ~30 cols of raw cell values for the chooser preview. */
  preview: string[][]
}

export interface ExcelFileInfo {
  /** "text" when the file's bytes don't match the Excel magic; frontend
   *  should fall back to single-file ingest, which routes through the
   *  CSV path automatically. */
  kind: 'excel' | 'text'
  this_path: string
  sheets: ExcelSheetInfo[]
}

export interface ExcelSheetSpec {
  name: string
  header_row: number
  start_col: string
  end_col: string
}

export interface ParquetSetInfo {
  /** The file the user originally selected. */
  this_path: string
  /** Every .parquet file in the same directory (this_path included). */
  peers: string[]
  total_bytes: number
}

export interface IngestResult {
  name: string
  source_path: string
  format: 'csv' | 'parquet' | 'xlsx'
  materialized: boolean
  row_count: number
  col_count: number
  has_rejects: boolean
  columns: ColumnMeta[]
  delimiter: string | null
  encoding: string | null
  first_row: number | null
  /** Which step in the encoding ladder produced a clean load.  Null
   *  for non-text formats.  Useful for debugging; not shown in UI. */
  strategy?: string | null
}

export interface RowsResponse {
  columns: string[]
  rows: (string | number | boolean | null)[][]
  offset: number
  limit: number
}

export interface SortSpec {
  column: string
  dir: 'asc' | 'desc'
}

export interface SqlColumn {
  name: string
  type: string
}

export interface SqlResultOk {
  ok: true
  /** Name of the hidden workspace table holding the full result set,
   *  or null for DDL / DML / PRAGMA with no result rows. */
  result_table: string | null
  columns: SqlColumn[]
  /** First window of rows (up to FIRST_WINDOW_SIZE).  Further windows
   *  are fetched via ``getRows(result_table, offset, limit)``. */
  rows: (string | number | boolean | null)[][]
  /** Total rows produced by the query.  Independent of ``rows.length``. */
  row_count: number
  /** Always false now; results are materialized, not capped. */
  truncated: boolean
  elapsed_ms: number
}

export interface SqlResultError {
  ok: false
  error: string
  elapsed_ms: number
}

export type SqlResult = SqlResultOk | SqlResultError

// --- column profiler -------------------------------------------------- //
export interface ColumnProfile {
  column: string
  type: string
  kind: 'numeric' | 'varchar' | 'temporal' | 'other'
  column_position: number
  common: {
    row_count: number
    non_null_count: number
    distinct_count: number
    null_count: number
    fill_pct: number
    cardinality_pct: number
    min: string | null
    max: string | null
    top_values: { value: string | null; count: number; percent: number }[]
  }
  branch: Record<string, unknown>
}

// --- derive ----------------------------------------------------------- //
export interface CastSuggestion {
  source: string
  source_type: string
  suggested_type: string | null
  cast_coverage: Record<string, number>
  sample_values: string[]
  /** Set when the suggested DATE / TIMESTAMP uses a non-ISO format and
   *  therefore needs `try_strptime(col, fmt)` on the cast side. */
  detected_date_format: string | null
}

export interface CastSpec {
  source: string
  target: string
  cast_to: string | null
  strict?: boolean
  date_format?: string | null
}

export interface DerivePreview {
  columns: SqlColumn[]
  rows: (string | number | boolean | null)[][]
  sql: string
}

export interface DeriveCreated {
  ok: boolean
  name: string
  row_count: number
  sql: string
}

// --- SQL Server export --------------------------------------------- //
export interface SqlServerConnectionOptions {
  server: string
  database: string
  auth: 'sql' | 'windows'
  username?: string | null
  password?: string | null
  port?: number | null
  azure?: boolean
}

// --------------------------------------------------------------------------- //
// Pluggable DB drivers (Import + future Export refactor)                       //
// --------------------------------------------------------------------------- //

export interface DriverFieldSpec {
  key: string
  label: string
  kind: 'text' | 'password' | 'number' | 'select'
  required: boolean
  default: unknown
  placeholder: string | null
  options: string[] | null
}

export interface DriverInfo {
  id: string
  display_name: string
  capabilities: ('export' | 'import' | 'ddl')[]
  connection_form: DriverFieldSpec[]
}

export interface RemoteTable {
  schema: string
  name: string
  kind: 'table' | 'view'
  row_count: number
}

export type ImportSource =
  | { kind: 'table'; database: string; schema: string; table: string }
  | { kind: 'query'; database: string | null; sql: string }

export interface ExportColumnMapping {
  source: string
  target: string
  sql_type: string
  nullable: boolean
  /** DuckDB source type; carried through so the backend can size
   *  pyodbc parameter buffers without re-probing the catalog. */
  source_type?: string
}

export interface ExportSource {
  kind: 'table' | 'query'
  name: string | null
  sql: string | null
}

export interface SqlServerExportOptions {
  source: ExportSource
  schema: string
  table_name: string
  mappings: ExportColumnMapping[]
  if_exists: 'fail' | 'drop' | 'append'
}

export interface ExportMappingSuggestion extends ExportColumnMapping {
  source_type: string
}

export interface ExportProgress {
  phase: 'idle' | 'connecting' | 'creating' | 'inserting' | 'verify' | 'writing' | 'done' | 'error'
  rows_written: number
  total_rows: number
  chunk_no: number
  chunk_rows: number
  last_chunk_ms: number
  elapsed_ms: number
  error: string | null
}

export interface ExportErrorRow {
  row: number
  marked: string[]
  values: Record<string, string | number | boolean | null>
  message: string
}

export interface ExportResult {
  ok: boolean
  rows_written?: number
  elapsed_ms: number
  error?: string
  error_rows?: ExportErrorRow[]
}

export interface OdbcDriverCheck {
  ok: boolean
  drivers: string[]
  required: string
}

export type FileTarget =
  | { kind: 'flat';    path: string; delimiter: string; header: boolean; quote: string; null: string; line_ending: 'crlf' | 'lf' }
  | { kind: 'parquet'; path: string; compression: 'snappy' | 'zstd' | 'gzip' | 'uncompressed' }
  | { kind: 'excel';   path: string; sheet_name: string }

export interface FileExportResult {
  ok: boolean
  rows_written?: number
  path?: string
  elapsed_ms: number
  error?: string
}

interface PywebviewApi {
  ping(): Promise<PingResponse>
  set_window_title(title: string): Promise<{ ok: boolean }>
  clipboard_read(): Promise<string>
  open_file_dialog(): Promise<string | null>
  save_file_dialog(default_name: string, file_types?: string[]): Promise<string | null>
  load_file(path: string, opts?: IngestOptions): Promise<IngestResult>
  preview_file_lines(path: string, n?: number, encoding?: string | null): Promise<{ lines: string[]; encoding: string; sniffed_delimiter: string }>
  inspect_parquet_set(path: string): Promise<ParquetSetInfo>
  inspect_excel_file(path: string): Promise<ExcelFileInfo>
  load_excel_workbook(path: string, sheet_specs: ExcelSheetSpec[], opts?: IngestOptions): Promise<IngestResult[]>
  list_tables(): Promise<TableInfo[]>
  get_table_schema(name: string): Promise<ColumnMeta[]>
  get_rows(name: string, offset: number, limit: number): Promise<RowsResponse>
  rename_table(old: string, newName: string): Promise<{ ok: boolean; name?: string }>
  drop_table(name: string): Promise<{ ok: boolean; error?: string }>
  materialize_table(name: string): Promise<{ ok: boolean }>
  run_sql(sql_text: string): Promise<SqlResult>
  cancel_sql(): Promise<{ cancelled: boolean }>
  save_query_as_table(sql_text: string, table_name: string): Promise<{ ok: boolean; name: string; row_count: number }>
  profile_column(table_name: string, column_name: string): Promise<ColumnProfile>
  profile_query_column(source_sql: string, column_name: string): Promise<ColumnProfile>
  suggest_casts(table_name: string): Promise<CastSuggestion[]>
  preview_derive(
    source_table: string,
    casts: CastSpec[],
    where_clause?: string | null,
    limit?: number,
  ): Promise<DerivePreview>
  derive_sql(
    source_table: string,
    new_name: string,
    casts: CastSpec[],
    where_clause?: string | null,
  ): Promise<{ sql: string }>
  create_derived_table(
    source_table: string,
    new_name: string,
    casts: CastSpec[],
    where_clause?: string | null,
  ): Promise<DeriveCreated>
  auto_derive(source_table: string, new_name: string): Promise<DeriveCreated>
  generate_ddl(table_name: string, dialect: 'duckdb' | 'sqlserver'): Promise<{ sql: string }>
  check_odbc_driver(): Promise<OdbcDriverCheck>
  test_sqlserver_connection(conn_opts: SqlServerConnectionOptions): Promise<{ ok: boolean; server_version?: string; error?: string }>
  list_sqlserver_databases(conn_opts: SqlServerConnectionOptions): Promise<string[]>
  list_sqlserver_schemas(conn_opts: SqlServerConnectionOptions): Promise<string[]>
  suggest_export_mappings(source: ExportSource): Promise<ExportMappingSuggestion[]>
  preview_export_ddl(export_opts: SqlServerExportOptions): Promise<{ sql: string }>
  export_to_sqlserver(
    conn_opts: SqlServerConnectionOptions,
    export_opts: SqlServerExportOptions,
  ): Promise<ExportResult>
  cancel_export(): Promise<{ cancelled: boolean }>
  get_export_progress(): Promise<ExportProgress>
  export_to_file(source: ExportSource, target: FileTarget, mappings?: ExportColumnMapping[]): Promise<FileExportResult>
  get_file_export_progress(): Promise<ExportProgress>
  cancel_file_export(): Promise<{ cancelled: boolean }>
  list_dialects(): Promise<{ id: string; display_name: string }[]>
  list_drivers(): Promise<DriverInfo[]>
  driver_is_available(driver_id: string): Promise<{ ok: boolean; [k: string]: unknown }>
  driver_test_connection(driver_id: string, conn_opts: Record<string, unknown>): Promise<{ ok: boolean; server_version?: string; error?: string }>
  driver_list_databases(driver_id: string, conn_opts: Record<string, unknown>): Promise<string[]>
  driver_list_schemas(driver_id: string, conn_opts: Record<string, unknown>, database: string): Promise<string[]>
  driver_list_tables(driver_id: string, conn_opts: Record<string, unknown>, database: string, schema: string): Promise<RemoteTable[]>
  driver_import_from_db(driver_id: string, conn_opts: Record<string, unknown>, source: ImportSource, target_name: string, row_cap?: number | null): Promise<IngestResult & { ok: boolean; elapsed_ms: number; error?: string }>
  driver_cancel_import(driver_id: string): Promise<{ ok: boolean }>
}

declare global {
  interface Window {
    pywebview?: { api: PywebviewApi }
  }
}

let cached: PywebviewApi | null = null
let pending: Promise<PywebviewApi> | null = null

function waitForApi(): Promise<PywebviewApi> {
  if (cached) return Promise.resolve(cached)
  if (pending) return pending

  pending = new Promise<PywebviewApi>((resolve, reject) => {
    if (window.pywebview?.api) {
      cached = window.pywebview.api
      return resolve(cached)
    }
    const onReady = () => {
      if (window.pywebview?.api) {
        cached = window.pywebview.api
        resolve(cached)
      } else {
        reject(new Error('pywebviewready fired but window.pywebview.api is missing'))
      }
    }
    window.addEventListener('pywebviewready', onReady, { once: true })
    setTimeout(() => {
      if (!cached) reject(new Error('Not running inside pywebview; window.pywebview was not injected'))
    }, 3000)
  })
  return pending
}

/** Run an operation against the bridge; waits for pywebview on first call. */
async function withApi<T>(fn: (api: PywebviewApi) => Promise<T>): Promise<T> {
  const api = await waitForApi()
  return fn(api)
}

export const ping            = ()                                    => withApi(a => a.ping())
export const setWindowTitle  = (title: string)                        => withApi(a => a.set_window_title(title))
export const openFileDialog  = ()                                    => withApi(a => a.open_file_dialog())
export const saveFileDialog  = (defaultName: string, fileTypes?: string[]) => withApi(a => a.save_file_dialog(defaultName, fileTypes))
export const loadFile        = (path: string, opts?: IngestOptions)  => withApi(a => a.load_file(path, opts))
export const previewFileLines = (path: string, n = 30, encoding?: string | null) =>
  withApi(a => a.preview_file_lines(path, n, encoding ?? null))
export const inspectParquetSet = (path: string)                       => withApi(a => a.inspect_parquet_set(path))
export const inspectExcelFile  = (path: string)                       => withApi(a => a.inspect_excel_file(path))
export const loadExcelWorkbook = (path: string, specs: ExcelSheetSpec[], opts?: IngestOptions) =>
  withApi(a => a.load_excel_workbook(path, specs, opts))
export const listTables      = ()                                    => withApi(a => a.list_tables())
export const getTableSchema  = (name: string)                        => withApi(a => a.get_table_schema(name))
export const getRows         = (name: string, offset = 0, limit = 500) =>
  withApi(a => a.get_rows(name, offset, limit))
export const renameTable     = (oldName: string, newName: string)    => withApi(a => a.rename_table(oldName, newName))
export const dropTable       = (name: string)                        => withApi(a => a.drop_table(name))
export const materializeTable = (name: string)                       => withApi(a => a.materialize_table(name))
export const runSql           = (sql: string)                        => withApi(a => a.run_sql(sql))
export const cancelSql        = ()                                   => withApi(a => a.cancel_sql())
export const saveQueryAsTable = (sql: string, tableName: string)     => withApi(a => a.save_query_as_table(sql, tableName))
export const profileColumn    = (t: string, c: string)               => withApi(a => a.profile_column(t, c))
export const profileQueryColumn = (sql: string, c: string)           => withApi(a => a.profile_query_column(sql, c))
export const suggestCasts     = (t: string)                          => withApi(a => a.suggest_casts(t))
export const previewDerive    = (t: string, casts: CastSpec[], where?: string | null, limit = 20) =>
  withApi(a => a.preview_derive(t, casts, where ?? null, limit))
export const createDerivedTable = (t: string, newName: string, casts: CastSpec[], where?: string | null) =>
  withApi(a => a.create_derived_table(t, newName, casts, where ?? null))
export const deriveSql = (t: string, newName: string, casts: CastSpec[], where?: string | null) =>
  withApi(a => a.derive_sql(t, newName, casts, where ?? null))
export const autoDerive       = (t: string, newName: string)         => withApi(a => a.auto_derive(t, newName))
export const generateDdl      = (t: string, dialect: 'duckdb' | 'sqlserver') => withApi(a => a.generate_ddl(t, dialect))

// --- SQL Server export ------------------------------------------- //
export const checkOdbcDriver         = ()                                 => withApi(a => a.check_odbc_driver())
export const testSqlServerConnection = (o: SqlServerConnectionOptions)    => withApi(a => a.test_sqlserver_connection(o))
export const listSqlServerDatabases  = (o: SqlServerConnectionOptions)    => withApi(a => a.list_sqlserver_databases(o))
export const listSqlServerSchemas    = (o: SqlServerConnectionOptions)    => withApi(a => a.list_sqlserver_schemas(o))
export const suggestExportMappings   = (src: ExportSource)                => withApi(a => a.suggest_export_mappings(src))
export const previewExportDdl        = (e: SqlServerExportOptions)        => withApi(a => a.preview_export_ddl(e))
export const exportToSqlServer       = (c: SqlServerConnectionOptions, e: SqlServerExportOptions) =>
  withApi(a => a.export_to_sqlserver(c, e))
export const cancelExport            = ()                                 => withApi(a => a.cancel_export())
export const getExportProgress       = ()                                 => withApi(a => a.get_export_progress())

// --- File export --------------------------------------------- //
export const exportToFile          = (src: ExportSource, t: FileTarget, mappings?: ExportColumnMapping[]) =>
  withApi(a => a.export_to_file(src, t, mappings))
export const getFileExportProgress = ()                                  => withApi(a => a.get_file_export_progress())
export const cancelFileExport      = ()                                  => withApi(a => a.cancel_file_export())

// Pluggable DB drivers (Import + future Export refactor)
export const listDialects          = ()                                  => withApi(a => a.list_dialects())
export const listDrivers           = ()                                  => withApi(a => a.list_drivers())
export const driverIsAvailable     = (id: string)                        => withApi(a => a.driver_is_available(id))
export const driverTestConnection  = (id: string, c: Record<string, unknown>) => withApi(a => a.driver_test_connection(id, c))
export const driverListDatabases   = (id: string, c: Record<string, unknown>) => withApi(a => a.driver_list_databases(id, c))
export const driverListSchemas     = (id: string, c: Record<string, unknown>, db: string) => withApi(a => a.driver_list_schemas(id, c, db))
export const driverListTables      = (id: string, c: Record<string, unknown>, db: string, schema: string) => withApi(a => a.driver_list_tables(id, c, db, schema))
export const driverImportFromDb    = (id: string, c: Record<string, unknown>, src: ImportSource, target: string, rowCap: number | null = 1_000_000) =>
  withApi(a => a.driver_import_from_db(id, c, src, target, rowCap))
export const driverCancelImport    = (id: string)                        => withApi(a => a.driver_cancel_import(id))
