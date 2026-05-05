<script setup lang="ts">
// A single-page tour of File Quacker's main features.  Triggered from
// the TopBar help icon; closed via X or the explicit Close button.

import { onBeforeUnmount, onMounted, ref } from 'vue'
import { X } from 'lucide-vue-next'
import { APP_VERSION } from '../lib/version'
import { useFocusTrap } from '../composables/useFocusTrap'

const emit = defineEmits<{ (e: 'close'): void }>()

const dialogRef = ref<HTMLElement | null>(null)
useFocusTrap(dialogRef)

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-[1px]">
    <div
      ref="dialogRef"
      class="flex flex-col rounded-md border border-border bg-surface-1 shadow-xl"
      style="height: min(820px, 92vh); width: min(880px, 94vw);"
      role="dialog"
      aria-modal="true"
      aria-labelledby="help-title"
    >
      <!-- Header -->
      <div class="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <h2 id="help-title" class="text-sm font-medium text-ink">File Quacker – help</h2>
        <span class="text-2xs text-ink-subtle">v{{ APP_VERSION }}</span>
        <button type="button" class="btn-icon ml-auto" aria-label="Close" title="Close" @click="$emit('close')">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- Scrollable body -->
      <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5 text-xs leading-relaxed text-ink">
        <p class="mb-5 text-ink-muted">
          A quick tour of what File Quacker does and how to use it.
          Everything runs locally; your data stays on your machine
          unless you choose to export it.
        </p>

        <!-- Load a file -->
        <section class="help-section">
          <h3 class="help-h">Load a file</h3>
          <p>
            Click the folder icon at the top of the left sidebar, or drop
            a file anywhere on the app window. Supported formats:
          </p>
          <ul class="help-ul">
            <li>CSV, TSV, and other delimited text files.</li>
            <li>Parquet. If the file you opened has sibling Parquet files in the same folder (Spark / Hive output), File Quacker offers to load just the one file or all of them as a single unioned dataset.</li>
            <li>Excel (.xlsx, .xlsm, .xls). When the workbook has more than one sheet, or when File Quacker detects a preamble (data starting at, say, B3 with title rows above), a chooser appears so you can pick which sheets to load and adjust where the data table starts. If the file is actually a text file with a misleading Excel extension (a common ELT pipeline output), it's loaded through the CSV path automatically.</li>
          </ul>
        </section>

        <!-- Sidebar -->
        <section class="help-section">
          <h3 class="help-h">Sidebar (table list)</h3>
          <p>
            Click a table to open it. The small chevron expands the column
            list. Drag a table or column name into the SQL editor to
            insert it at the cursor.
          </p>
          <p>
            Two icons sit at the top-right of the Tables header. The
            folder icon opens a file (CSV / Parquet / Excel). The
            lightning-database icon opens <strong>Import from database</strong>:
            connect to SQL Server (more drivers coming), browse the
            catalog, pick a table or write a query, and stream rows
            into a workspace table. A 1,000,000-row safety cap is on
            by default — uncheck the cap to import the full source.
          </p>
          <p>The <strong>⋯ menu</strong> on each row offers:</p>
          <ul class="help-ul">
            <li><strong>Rename…</strong> – changes the table's name.</li>
            <li><strong>Clone…</strong> – creates a typed copy. The dialog suggests a type for each column with a percent showing how much of the data fits, and you can preview before committing.</li>
            <li><strong>Clone with Auto-type</strong> – accepts every suggested type in one click.</li>
            <li><strong>Show DDL…</strong> – opens a right drawer with a CREATE TABLE statement in your choice of dialect (DuckDB or SQL Server). Click Copy to put it on the clipboard.</li>
            <li><strong>Export…</strong> – see the Export section below.</li>
            <li><strong>Materialize</strong> – turns a view into a real table.</li>
            <li><strong>Drop</strong> – removes the table from the workspace. Your source file is not touched.</li>
          </ul>
        </section>

        <!-- Grid view -->
        <section class="help-section">
          <h3 class="help-h">Grid view</h3>
          <ul class="help-ul">
            <li>Click a column header to sort (ascending, then descending, then off).</li>
            <li>Click the small bar-chart icon in a header to open the <strong>Column profile</strong> drawer.</li>
            <li>Drag a column's right edge to resize. Double-click that edge to auto-fit it to the widest visible value.</li>
          </ul>

          <p class="mt-2"><strong>Selection and copy</strong></p>
          <ul class="help-ul">
            <li>Click a cell to select it. Drag to select a range. Hold <span class="help-kbd">Shift</span> and click to extend the selection.</li>
            <li>Click the row-number column on the left to select an entire row. Drag or Shift+click to select multiple rows.</li>
            <li>Press <span class="help-kbd">Ctrl+C</span> to copy the selection. A single cell copies as the raw value; a range copies as tab-separated values you can paste straight into Excel or a text file.</li>
            <li>Right-click a cell for <em>Copy</em> or <em>Copy with Headers</em>. Right-click the row-number column for <em>Pivot Row</em>, plus the same copy options.</li>
            <li>Press <span class="help-kbd">Esc</span> to close the menu or clear the selection.</li>
          </ul>

          <p class="mt-2">
            The <strong>Row pivot</strong> panel shows a single row turned
            on its side, which is handy for wide tables. Open it from the
            row-number column's right-click menu.
          </p>

          <p class="mt-2 text-ink-subtle">
            The same selection, copy, and pivot behavior is available in
            the SQL results grid.
          </p>
        </section>

        <!-- SQL editor -->
        <section class="help-section">
          <h3 class="help-h">SQL editor</h3>
          <p>
            Switch to <strong>SQL</strong> mode from the top bar to write
            and run queries against any of your loaded tables.
          </p>
          <ul class="help-ul">
            <li>Run a query with the <strong>Run</strong> button, <span class="help-kbd">Ctrl+Enter</span>, or <span class="help-kbd">F5</span>. If you have text selected, only that selection runs; otherwise the whole editor runs.</li>
            <li>Press <span class="help-kbd">Esc</span> to cancel a running query.</li>
            <li><strong>Save result as table</strong> – stores the last query's output as a new workspace table.</li>
            <li><strong>Export result…</strong> – opens the Export dialog using the current query as the source, so you can export without saving it first.</li>
            <li>Drag a table or column from the sidebar to insert it at the cursor.</li>
            <li>Right-click the editor for <em>Cut</em>, <em>Copy</em>, <em>Paste</em>, and <em>Run Selection</em> or <em>Run Query</em> (the label changes depending on whether text is selected).</li>
            <li>Editor conveniences: SQL syntax highlighting, bracket matching, auto-closing of <span class="help-kbd">(</span> <span class="help-kbd">[</span> <span class="help-kbd">{</span> <span class="help-kbd">'</span> <span class="help-kbd">"</span>, and an autocomplete popup that suggests SQL keywords plus the names of your loaded tables and (once expanded in the sidebar) their columns. Press <span class="help-kbd">Tab</span> or <span class="help-kbd">Enter</span> to accept the highlighted suggestion. <span class="help-kbd">Tab</span> outside the popup inserts four spaces.</li>
            <li>Hold <span class="help-kbd">Alt</span> and drag to make a rectangular (block / column) selection across multiple lines.</li>
            <li>The results grid supports the same sorting, selection, copy, profile, and pivot actions as the main grid.</li>
          </ul>
        </section>

        <!-- Export -->
        <section class="help-section">
          <h3 class="help-h">Export</h3>
          <p>The Export dialog can run on either source:</p>
          <ul class="help-ul">
            <li><strong>Table</strong> – opened from the sidebar's ⋯ menu on any table.</li>
            <li><strong>Query result</strong> – opened from the SQL editor's <em>Export result…</em> button. Filter and shape the data with SQL, then export the result directly.</li>
          </ul>
          <p>Available targets:</p>
          <ul class="help-ul">
            <li><strong>SQL Server</strong> – sign in with SQL or Windows authentication, pick a database and schema, and choose what to do if the table already exists (Fail, Drop and recreate, or Append).</li>
            <li><strong>Flat file</strong> – choose a delimiter (Pipe, Comma, Tab, Semicolon, or your own), and configure header, quoting, NULL representation, and line endings (CRLF or LF; CRLF is the default).</li>
            <li><strong>Parquet</strong> – with your choice of compression.</li>
            <li><strong>Excel (.xlsx)</strong> – with a configurable sheet name.</li>
          </ul>
          <p>
            The <strong>column mapping</strong> pane lets you rename target
            columns. For SQL Server you can also adjust each column's type
            and nullability before the export runs.
          </p>
          <p>
            Any credentials you enter stay in the dialog and are forgotten
            as soon as it closes.
          </p>
        </section>

        <!-- Panels -->
        <section class="help-section">
          <h3 class="help-h">Right-hand drawers</h3>
          <p>
            Only one drawer is open at a time. Opening another closes the
            previous one.
          </p>
          <ul class="help-ul">
            <li><strong>Column profile</strong> – shows an overview of a column: top values, null and fill rates, and numeric, text, or date-specific stats. It works for both tables and query results.</li>
            <li><strong>Row pivot</strong> – shows a single row as three columns (#, Column, Value). Copy the contents straight into a spreadsheet.</li>
            <li><strong>DDL</strong> – shows a CREATE TABLE statement in DuckDB or SQL Server form, with column types sized from the actual data.</li>
          </ul>
        </section>

        <!-- Theme + layout -->
        <section class="help-section">
          <h3 class="help-h">Theme and layout</h3>
          <ul class="help-ul">
            <li>The theme toggle in the top-right cycles System, Light, and Dark. Your choice is remembered across sessions.</li>
            <li>You can drag to resize the sidebar, the right drawer, and the SQL editor / results split. All sizes are remembered.</li>
          </ul>
        </section>

        <!-- Shortcuts -->
        <section class="help-section">
          <h3 class="help-h">Keyboard shortcuts</h3>
          <table class="mt-1 border-collapse text-xs">
            <tbody>
              <tr><td class="help-kbd-cell"><span class="help-kbd">Ctrl+Enter</span> / <span class="help-kbd">F5</span></td><td class="pl-4">Run the SQL query (selection if any, otherwise the whole editor)</td></tr>
              <tr><td class="help-kbd-cell"><span class="help-kbd">Esc</span></td><td class="pl-4">Cancel a running query, close a right-click menu, clear the grid selection, or close help</td></tr>
              <tr><td class="help-kbd-cell"><span class="help-kbd">Ctrl+C</span></td><td class="pl-4">Copy the grid selection (single cell as raw value, range as TSV)</td></tr>
              <tr><td class="help-kbd-cell"><span class="help-kbd">Shift</span>+click</td><td class="pl-4">Extend the grid selection from the previously clicked cell or row</td></tr>
              <tr><td class="help-kbd-cell">Right-click</td><td class="pl-4">On a cell: Copy or Copy with Headers. On a row number: also Pivot Row.</td></tr>
            </tbody>
          </table>
        </section>

        <p class="mt-6 text-ink-subtle">
          Found a bug or have a feature request? See
          <code>CONTRIBUTING.md</code> in the project repo for how to
          open an issue.
        </p>
      </div>

      <!-- Footer -->
      <div class="flex h-11 shrink-0 items-center justify-end border-t border-border px-3">
        <button type="button" class="btn-secondary" @click="$emit('close')">
          Close
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.help-section {
  @apply mb-6;
}
.help-h {
  @apply mb-2 text-sm font-semibold text-ink;
}
.help-section p {
  @apply mb-2 text-ink-secondary;
}
.help-ul {
  @apply mb-2 ml-5 list-disc space-y-1 text-ink-secondary;
}
.help-kbd {
  @apply inline-block rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-ink;
}
.help-code {
  @apply rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px] text-ink;
}
.help-kbd-cell {
  @apply whitespace-nowrap py-1 pr-4 align-top;
}
</style>
