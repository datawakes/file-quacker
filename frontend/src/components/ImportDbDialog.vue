<script setup lang="ts">
// Import-from-DB dialog.  Mirror of the file-ingest path: connect to
// a remote database, browse the catalog, pick a table or write a
// query, name the new table, and stream the rows in.  A 1M-row
// safety cap is on by default so a careless click doesn't try to
// pull a billion rows; the user can disable it explicitly.
//
// V1 supports SQL Server only; the connection form is rendered
// generically from the driver's `connection_form` spec so adding
// Postgres / MySQL later is purely a backend addition.

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { Database, X } from 'lucide-vue-next'
import { useViewStore } from '../stores/view'
import { useTablesStore } from '../stores/tables'
import { useToasts } from '../stores/toasts'
import { useFocusTrap } from '../composables/useFocusTrap'

const dialogRef = ref<HTMLElement | null>(null)
useFocusTrap(dialogRef)
import ConnectionProfileBar from './ConnectionProfileBar.vue'
import {
  driverCancelImport,
  driverImportFromDb,
  driverListDatabases,
  driverListSchemas,
  driverListTables,
  driverTestConnection,
  listDrivers,
  type DriverFieldSpec,
  type DriverInfo,
  type ImportSource,
  type RemoteTable,
} from '../lib/api'

const viewStore = useViewStore()
const tables = useTablesStore()
const toasts = useToasts()
const { importDb } = storeToRefs(viewStore)

function close() {
  if (importing.value) return                       // don't drop a running import
  viewStore.closeImportDb()
}
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') close() }
onMounted(() => { window.addEventListener('keydown', onKey); void loadDrivers() })
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

// ---- driver discovery ---- //
const drivers = ref<DriverInfo[]>([])
const driverId = ref<string>('sqlserver')
const activeDriver = computed(() =>
  drivers.value.find(d => d.id === driverId.value) ?? null,
)
// `database` belongs in the Source section for import; the user
// picks it after testing the connection.  We hide it from the
// connection form here; export (which does need a target database
// up front) keeps it in its own UI.
const importConnectionFields = computed(() =>
  (activeDriver.value?.connection_form ?? []).filter(f => f.key !== 'database'),
)
// Vault-backed field keys for this driver (drives the profile bar's
// "clear on select" + the Save-password affordance).
const secretKeys = computed(() =>
  (activeDriver.value?.connection_form ?? []).filter(f => f.secret).map(f => f.key),
)
// Active saved profile, if any.  Passed through every connection call so
// the backend resolves the secret server-side (it never reaches here).
const activeProfileId = ref<string | null>(null)
// Char count per saved secret field for the active profile; drives the
// masked placeholder dots on the (intentionally blank) password input.
const profileSecretLengths = ref<Record<string, number>>({})

function maskedPlaceholder(f: DriverFieldSpec): string {
  const n = f.kind === 'password' ? (profileSecretLengths.value[f.key] ?? 0) : 0
  return n > 0 ? '•'.repeat(n) : (f.placeholder ?? '')
}
async function loadDrivers() {
  try {
    drivers.value = await listDrivers()
    // Default to the first driver that supports import.
    const importable = drivers.value.filter(d => d.capabilities.includes('import'))
    if (importable.length) driverId.value = importable[0].id
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  }
}

// ---- connection form (FieldSpec-driven) ---- //
const connOpts = ref<Record<string, unknown>>({})
watch(activeDriver, (d) => {
  // Re-seed defaults when the user swaps drivers.
  const next: Record<string, unknown> = {}
  for (const f of d?.connection_form ?? []) next[f.key] = f.default ?? ''
  connOpts.value = next
  activeProfileId.value = null            // selection doesn't carry across drivers
}, { immediate: true })

watch(importDb, (open) => {
  // Reset transient state every time the dialog opens.  Connection
  // form values stay (handy if the user reopens to import another
  // table from the same server).
  if (open) {
    testResult.value = null
    databases.value = []
    schemas.value = []
    remoteTables.value = []
    pickedDatabase.value = ''
    pickedSchema.value = ''
    pickedTable.value = ''
    customSql.value = ''
    targetName.value = ''
    importing.value = false
    progress.value = ''
  }
})

// ---- catalog browse ---- //
const testResult = ref<{ ok: boolean; message: string } | null>(null)
const testing = ref(false)
const databases = ref<string[]>([])
const schemas = ref<string[]>([])
const remoteTables = ref<RemoteTable[]>([])
const pickedDatabase = ref<string>('')
const pickedSchema = ref<string>('')
const pickedTable = ref<string>('')
const useQuery = ref(false)
const customSql = ref<string>('')

async function onTest() {
  if (!driverId.value) return
  testing.value = true
  testResult.value = null
  try {
    const r = await driverTestConnection(driverId.value, connOpts.value, activeProfileId.value)
    if (r.ok) {
      testResult.value = { ok: true, message: r.server_version || 'connected' }
      databases.value = await driverListDatabases(driverId.value, connOpts.value, activeProfileId.value)
    } else {
      testResult.value = { ok: false, message: r.error || 'connection failed' }
    }
  } catch (e) {
    testResult.value = { ok: false, message: e instanceof Error ? e.message : String(e) }
  } finally {
    testing.value = false
  }
}

watch(pickedDatabase, async (db) => {
  schemas.value = []
  remoteTables.value = []
  pickedSchema.value = ''
  pickedTable.value = ''
  if (!db || !driverId.value) return
  try {
    schemas.value = await driverListSchemas(driverId.value, connOpts.value, db, activeProfileId.value)
    if (schemas.value.includes('dbo')) pickedSchema.value = 'dbo'
    else if (schemas.value.length === 1) pickedSchema.value = schemas.value[0]
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  }
})

watch([pickedDatabase, pickedSchema], async ([db, schema]) => {
  remoteTables.value = []
  pickedTable.value = ''
  if (!db || !schema || !driverId.value) return
  try {
    remoteTables.value = await driverListTables(driverId.value, connOpts.value, db, schema, activeProfileId.value)
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  }
})

watch(pickedTable, (t) => {
  if (t && !targetName.value) targetName.value = toSnakeCase(t)
})

// In Custom SQL mode, seed the table name from the first table
// referenced in the user's `from` clause; if nothing parseable, fall
// back to a generic label so the Import button doesn't sit disabled
// just because the user hasn't typed a name.  We only seed when the
// field is empty so we never clobber an explicit user choice.
watch([useQuery, customSql], ([on, sql]) => {
  if (!on) return
  if (targetName.value) return
  const guess = guessTableFromSql(sql) || 'query_result'
  targetName.value = toSnakeCase(guess)
})

function toSnakeCase(s: string): string {
  return s.replace(/[ .-]+/g, '_').replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase()
}

function guessTableFromSql(sql: string): string {
  // Identifier after the LAST `from` in the SQL.  For simple queries
  // there's only one FROM, so this matches the obvious source.  For
  // CTEs the inner FROM(s) appear first and the outermost `select
  // ... from <cte_alias>` appears last; taking the last match
  // names the result after the CTE alias, which is what the user
  // actually wrote at the top level.  Doesn't have to be perfect;
  // the user can edit.
  const all = [...sql.matchAll(/\bfrom\s+([\[\]"`\w.]+)/gi)]
  if (!all.length) return ''
  const last = all[all.length - 1][1]
  const ref = last.replace(/[\[\]"`]/g, '')
  const parts = ref.split('.')
  return parts[parts.length - 1] || ''
}

// ---- target + cap ---- //
const targetName = ref<string>('')
const useRowCap = ref<boolean>(true)
const rowCap = ref<number>(1_000_000)

const canImport = computed(() => {
  if (importing.value) return false
  if (!testResult.value?.ok) return false
  if (!targetName.value.trim()) return false
  if (useQuery.value) {
    return customSql.value.trim().length > 0
  }
  return !!pickedDatabase.value && !!pickedSchema.value && !!pickedTable.value
})

// ---- import ---- //
const importing = ref(false)
const progress = ref<string>('')

async function onImport() {
  if (!canImport.value) return
  importing.value = true
  progress.value = 'reading…'
  const cap = useRowCap.value ? rowCap.value : null
  const src: ImportSource = useQuery.value
    ? { kind: 'query', database: pickedDatabase.value || null, sql: customSql.value }
    : { kind: 'table', database: pickedDatabase.value, schema: pickedSchema.value, table: pickedTable.value }

  // Make sure the connection form's database is set so query mode
  // resolves objects in the right context.
  const opts = { ...connOpts.value }
  if (pickedDatabase.value) opts.database = pickedDatabase.value

  const toastId = toasts.showSticky(`Importing ${useQuery.value ? '(query)' : pickedTable.value}…`, 'info')
  try {
    const r = await driverImportFromDb(driverId.value, opts, src, targetName.value, cap, activeProfileId.value)
    if (r.ok) {
      toasts.dismiss(toastId)
      const capped = r.has_rejects ? ` (capped at ${cap?.toLocaleString()})` : ''
      toasts.show(`Imported ${r.row_count.toLocaleString()} rows as “${r.name}”${capped}`, 'success')
      await tables.refresh()
      await tables.selectTable(r.name)
      viewStore.closeImportDb()
    } else {
      toasts.dismiss(toastId)
      toasts.show(r.error || 'Import failed', 'error', 6000)
    }
  } catch (e) {
    toasts.dismiss(toastId)
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 6000)
  } finally {
    importing.value = false
    progress.value = ''
  }
}

async function onCancelImport() {
  if (!importing.value || !driverId.value) return
  try { await driverCancelImport(driverId.value) } catch { /* ignore */ }
}

function fieldType(f: DriverFieldSpec): string {
  if (f.kind === 'password') return 'password'
  if (f.kind === 'number')   return 'number'
  return 'text'
}
</script>

<template>
  <div
    v-if="importDb"
    class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-[1px]"
  >
    <div
      ref="dialogRef"
      class="flex flex-col rounded-md border border-border bg-surface-1 shadow-xl"
      style="height: min(740px, 92vh); width: min(900px, 96vw);"
      role="dialog"
      aria-modal="true"
      aria-labelledby="import-db-title"
    >
      <!-- Header -->
      <div class="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <Database class="h-4 w-4 text-ink-muted" />
        <h2 id="import-db-title" class="text-sm font-medium text-ink">
          Import from database
        </h2>
        <button type="button" class="btn-icon ml-auto" aria-label="Close" title="Close" :disabled="importing" @click="close">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- Body -->
      <div class="min-h-0 flex-1 overflow-y-auto p-4 text-xs">
        <!-- Driver picker -->
        <div class="mb-3 flex items-center gap-3">
          <label class="text-ink-muted">Driver</label>
          <select
            v-model="driverId"
            class="h-7 rounded border border-border bg-surface-0 px-2 text-xs outline-none focus:border-accent"
          >
            <option v-for="d in drivers" :key="d.id" :value="d.id">{{ d.display_name }}</option>
          </select>
        </div>

        <!-- Connection form (driven by the driver's FieldSpec) -->
        <fieldset class="mb-3 rounded border border-border p-3">
          <legend class="px-1 text-2xs uppercase tracking-wide text-ink-subtle">
            Connection
          </legend>
          <ConnectionProfileBar
            :driver-id="driverId"
            :conn-opts="connOpts"
            :secret-keys="secretKeys"
            field-width="346px"
            v-model:profile-id="activeProfileId"
            v-model:secret-lengths="profileSecretLengths"
          />
          <div class="grid gap-x-3 gap-y-1.5" style="grid-template-columns: 110px minmax(0, 1fr);">
            <template v-for="f in importConnectionFields" :key="f.key">
              <label class="self-center text-ink-muted">{{ f.label }}</label>
              <input
                v-if="f.kind === 'checkbox'"
                type="checkbox"
                v-model="connOpts[f.key]"
                class="h-4 w-4 self-center justify-self-start"
              />
              <select
                v-else-if="f.kind === 'select'"
                v-model="connOpts[f.key]"
                class="h-7 w-48 rounded border border-border bg-surface-0 px-2 outline-none focus:border-accent"
              >
                <option v-for="o in (f.options ?? [])" :key="o" :value="o">{{ o }}</option>
              </select>
              <input
                v-else
                :type="fieldType(f)"
                :placeholder="maskedPlaceholder(f)"
                v-model="connOpts[f.key]"
                class="h-7 w-[346px] rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
              />
            </template>
          </div>
          <div class="mt-2 flex items-center gap-2">
            <button type="button" class="btn-secondary" :disabled="testing" @click="onTest">
              {{ testing ? 'Testing…' : 'Test connection' }}
            </button>
            <span
              v-if="testResult"
              class="font-mono text-2xs"
              :class="testResult.ok ? 'text-success' : 'text-danger'"
              :title="testResult.message"
            >
              {{ testResult.ok ? '✓' : '✗' }} {{ testResult.message.slice(0, 80) }}
            </span>
          </div>
        </fieldset>

        <!-- Source picker -->
        <fieldset class="mb-3 rounded border border-border p-3" :disabled="!testResult?.ok">
          <legend class="px-1 text-2xs uppercase tracking-wide text-ink-subtle">
            Source
          </legend>
          <div class="mb-2 flex items-center gap-3">
            <label class="flex items-center gap-1.5">
              <input type="radio" :checked="!useQuery" @change="useQuery = false" />
              <span>Pick a table</span>
            </label>
            <label class="flex items-center gap-1.5">
              <input type="radio" :checked="useQuery" @change="useQuery = true" />
              <span>Custom SQL</span>
            </label>
          </div>

          <template v-if="!useQuery">
            <div class="grid gap-x-3 gap-y-1.5" style="grid-template-columns: 110px minmax(0, 1fr);">
              <label class="self-center text-ink-muted">Database</label>
              <select
                v-model="pickedDatabase"
                class="h-7 w-72 rounded border border-border bg-surface-0 px-2 outline-none focus:border-accent"
              >
                <option value="">- pick -</option>
                <option v-for="d in databases" :key="d" :value="d">{{ d }}</option>
              </select>
              <label class="self-center text-ink-muted">Schema</label>
              <select
                v-model="pickedSchema"
                class="h-7 w-72 rounded border border-border bg-surface-0 px-2 outline-none focus:border-accent"
              >
                <option value="">- pick -</option>
                <option v-for="s in schemas" :key="s" :value="s">{{ s }}</option>
              </select>
              <label class="self-center text-ink-muted">Table</label>
              <select
                v-model="pickedTable"
                class="h-7 w-72 rounded border border-border bg-surface-0 px-2 outline-none focus:border-accent"
              >
                <option value="">- pick -</option>
                <option v-for="t in remoteTables" :key="`${t.schema}.${t.name}`" :value="t.name">
                  {{ t.name }} ({{ t.row_count.toLocaleString() }} rows{{ t.kind === 'view' ? ', view' : '' }})
                </option>
              </select>
            </div>
          </template>
          <template v-else>
            <div class="grid gap-x-3 gap-y-1.5" style="grid-template-columns: 110px minmax(0, 1fr);">
              <label class="self-center text-ink-muted">Database</label>
              <select
                v-model="pickedDatabase"
                class="h-7 w-72 rounded border border-border bg-surface-0 px-2 outline-none focus:border-accent"
              >
                <option value="">(server-default)</option>
                <option v-for="d in databases" :key="d" :value="d">{{ d }}</option>
              </select>
              <label class="self-center pt-1 text-ink-muted">SQL</label>
              <textarea
                v-model="customSql"
                rows="6"
                placeholder="select top 1000 * from sales.dbo.orders where ..."
                class="rounded border border-border bg-surface-0 px-2 py-1 font-mono outline-none focus:border-accent"
              />
            </div>
          </template>
        </fieldset>

        <!-- Target -->
        <fieldset class="mb-3 rounded border border-border p-3" :disabled="!testResult?.ok">
          <legend class="px-1 text-2xs uppercase tracking-wide text-ink-subtle">
            Target
          </legend>
          <div class="grid gap-x-3 gap-y-1.5" style="grid-template-columns: 110px minmax(0, 1fr);">
            <label class="self-center text-ink-muted">Table name</label>
            <input
              v-model="targetName"
              type="text"
              placeholder="snake_cased name for the new table"
              class="h-7 w-72 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
            />
            <label class="self-center text-ink-muted">Row cap</label>
            <div class="flex items-center gap-2">
              <input type="checkbox" v-model="useRowCap" />
              <input
                v-model.number="rowCap"
                type="number"
                min="1"
                step="100000"
                :disabled="!useRowCap"
                class="h-7 w-32 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50"
              />
              <span class="text-2xs text-ink-subtle">
                Default 1,000,000.  Uncheck to import the full source.
              </span>
            </div>
          </div>
        </fieldset>
      </div>

      <!-- Footer -->
      <div class="flex h-12 shrink-0 items-center justify-end gap-2 border-t border-border px-3">
        <span v-if="importing" class="mr-auto font-mono text-2xs text-ink-subtle">
          {{ progress || 'importing…' }}
        </span>
        <button
          v-if="importing"
          type="button"
          class="btn-secondary"
          @click="onCancelImport"
        >Cancel import</button>
        <button type="button" class="btn-secondary" :disabled="importing" @click="close">Close</button>
        <button
          type="button"
          class="btn-primary"
          :disabled="!canImport"
          @click="onImport"
        >Import</button>
      </div>
    </div>
  </div>
</template>
