<script setup lang="ts">
// Connection + target form for SQL Server exports.
//
// Owned state: availableDatabases, availableSchemas, testState.
// The parent holds conn / schema / table / ifExists as refs and binds them
// here via v-model so the export button logic in ExportDialog can stay up-
// to-date without any extra event forwarding.

import { computed, ref, watch } from 'vue'
import {
  listSqlServerDatabases,
  listSqlServerSchemas,
  testSqlServerConnection,
  type SqlServerConnectionOptions,
} from '../../lib/api'

// --- props --------------------------------------------------------------- //
defineProps<{
  driverMissing: boolean
}>()

// --- v-model bindings ---------------------------------------------------- //
const conn     = defineModel<SqlServerConnectionOptions>('conn',     { required: true })
const schema   = defineModel<string>('schema',   { required: true })
const table    = defineModel<string>('table',    { required: true })
const ifExists = defineModel<'fail' | 'drop' | 'append'>('ifExists', { required: true })

// --- local state --------------------------------------------------------- //
const testState          = ref<{ kind: 'idle' | 'busy' | 'ok' | 'err'; message?: string }>({ kind: 'idle' })
const availableDatabases = ref<string[] | null>(null)   // non-null once Test has succeeded
const availableSchemas   = ref<string[] | null>(null)   // non-null once a DB has been picked

// Template refs so Tab from Server → User and User → Password skip past
// the Schema / Table fields on the right half of the grid.
const userRef     = ref<HTMLInputElement | null>(null)
const passwordRef = ref<HTMLInputElement | null>(null)

function onServerTab(e: KeyboardEvent) {
  if (e.shiftKey) return
  if (conn.value.auth === 'windows') return
  e.preventDefault()
  userRef.value?.focus()
}

function onUserTab(e: KeyboardEvent) {
  if (e.shiftKey) return
  e.preventDefault()
  passwordRef.value?.focus()
}

function onPasswordEnter() {
  if (credsFilled.value && testState.value.kind !== 'busy') onTest()
}

// --- computed ------------------------------------------------------------ //
// For the Test button; only needs server + creds.  Database picks come
// from the list_databases() call that Test fires off.
const credsFilled = computed(() => {
  if (!conn.value.server) return false
  if (conn.value.auth === 'windows') return true
  return !!(conn.value.username && conn.value.password)
})

// --- watchers ------------------------------------------------------------ //
// Any edit to a *server-level* field invalidates the database list
// (and therefore the schema list).  The database dropdown itself does
// NOT trigger this; changing DB just re-lists schemas below.
watch(
  () => [conn.value.server, conn.value.auth,
         conn.value.username, conn.value.password,
         conn.value.port, conn.value.azure],
  () => {
    availableDatabases.value = null
    availableSchemas.value   = null
    testState.value          = { kind: 'idle' }
  },
)

// Changing the selected database re-lists schemas scoped to that DB.
watch(() => conn.value.database, async (db) => {
  if (!db || availableDatabases.value === null) {
    availableSchemas.value = null
    return
  }
  try {
    const schemas = await listSqlServerSchemas(conn.value)
    availableSchemas.value = schemas
    if (!schemas.includes(schema.value)) {
      schema.value = schemas.includes('dbo') ? 'dbo' : (schemas[0] ?? '')
    }
  } catch {
    availableSchemas.value = null
  }
})

// --- functions ----------------------------------------------------------- //
async function onTest() {
  testState.value = { kind: 'busy' }
  try {
    const t = await testSqlServerConnection(conn.value)
    if (!t.ok) {
      testState.value          = { kind: 'err', message: t.error ?? 'connection failed' }
      availableDatabases.value = null
      availableSchemas.value   = null
      return
    }
    testState.value = { kind: 'ok', message: t.server_version }
    const dbs = await listSqlServerDatabases(conn.value)
    availableDatabases.value = dbs
    // Keep the current db if it's in the list; otherwise pick one.
    if (!conn.value.database || !dbs.includes(conn.value.database)) {
      conn.value.database = dbs[0] ?? ''
    } else {
      // Trigger schema list refresh even though db value didn't change.
      const schemas = await listSqlServerSchemas(conn.value)
      availableSchemas.value = schemas
      if (!schemas.includes(schema.value)) {
        schema.value = schemas.includes('dbo') ? 'dbo' : (schemas[0] ?? '')
      }
    }
  } catch (e) {
    testState.value          = { kind: 'err', message: e instanceof Error ? e.message : String(e) }
    availableDatabases.value = null
    availableSchemas.value   = null
  }
}
</script>

<template>
  <section class="shrink-0 border-b border-border p-3">
    <div
      class="grid gap-x-3 gap-y-1.5 text-xs"
      style="grid-template-columns: 80px minmax(0, 1fr) 32px 80px minmax(0, 1fr);"
    >
      <!-- Row 1: Server | Schema -->
      <label class="self-center text-ink-muted">Server</label>
      <input v-model="conn.server" type="text" :disabled="driverMissing"
        @keydown.tab="onServerTab"
        class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50" />
      <div />
      <label class="self-center text-ink-muted">Schema</label>
      <select v-if="availableSchemas" v-model="schema" :disabled="driverMissing"
        class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50">
        <option v-for="s in availableSchemas" :key="s" :value="s">{{ s }}</option>
      </select>
      <input v-else v-model="schema" type="text" :disabled="driverMissing"
        placeholder="click Test then pick a database"
        class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50" />

      <!-- Row 2: Auth | Table -->
      <label class="self-center text-ink-muted">Auth</label>
      <div class="flex items-center gap-3">
        <label class="flex items-center gap-1.5">
          <input type="radio" v-model="conn.auth" value="sql" :disabled="driverMissing" />
          <span>SQL</span>
        </label>
        <label class="flex items-center gap-1.5">
          <input type="radio" v-model="conn.auth" value="windows" :disabled="driverMissing" />
          <span>Windows</span>
        </label>
      </div>
      <div />
      <label class="self-center text-ink-muted">Table</label>
      <input v-model="table" type="text" :disabled="driverMissing"
        class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50" />

      <!-- Row 3: User (hidden for Windows) | On exists -->
      <template v-if="conn.auth !== 'windows'">
        <label class="self-center text-ink-muted">User</label>
        <input ref="userRef" v-model="conn.username" type="text" :disabled="driverMissing" autocomplete="off"
          @keydown.tab="onUserTab"
          class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50" />
      </template>
      <template v-else>
        <div /><div />
      </template>
      <div />
      <label class="self-center text-ink-muted">On exists</label>
      <div class="flex items-center gap-3">
        <label class="flex items-center gap-1.5">
          <input type="radio" v-model="ifExists" value="fail" :disabled="driverMissing" />
          <span>Fail</span>
        </label>
        <label class="flex items-center gap-1.5">
          <input type="radio" v-model="ifExists" value="drop" :disabled="driverMissing" />
          <span>Drop &amp; recreate</span>
        </label>
        <label class="flex items-center gap-1.5">
          <input type="radio" v-model="ifExists" value="append" :disabled="driverMissing" />
          <span>Append</span>
        </label>
      </div>

      <!-- Row 4: Password (hidden for Windows) | (blank) -->
      <template v-if="conn.auth !== 'windows'">
        <label class="self-center text-ink-muted">Password</label>
        <input ref="passwordRef" v-model="conn.password"
          type="password"
          :disabled="driverMissing" autocomplete="off"
          @keydown.enter="onPasswordEnter"
          class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50" />
        <div /><div /><div />
      </template>

      <!-- Row 5: Database (dropdown, populated on Test) -->
      <label class="self-center text-ink-muted">Database</label>
      <select v-if="availableDatabases" v-model="conn.database" :disabled="driverMissing"
        class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50">
        <option v-for="d in availableDatabases" :key="d" :value="d">{{ d }}</option>
      </select>
      <input v-else
        :value="conn.database"
        disabled
        placeholder="click Test to populate"
        class="h-7 rounded border border-border bg-surface-0 px-2 font-mono outline-none disabled:opacity-50" />
      <div /><div /><div />

      <!-- Row 6 (spans all columns): Options + Test -->
      <label class="self-center text-ink-muted">Options</label>
      <div class="col-span-4 flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <label class="flex items-center gap-1.5">
          <input type="checkbox" v-model="conn.azure" :disabled="driverMissing" />
          <span>Azure SQL</span>
        </label>
        <label class="flex items-center gap-1.5">
          <span class="text-ink-muted">Port</span>
          <input
            :value="conn.port ?? ''"
            @input="conn.port = ($event.target as HTMLInputElement).value
              ? Number(($event.target as HTMLInputElement).value) : null"
            type="number"
            placeholder="1433"
            :disabled="driverMissing"
            class="h-7 w-20 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50" />
        </label>
        <button
          type="button"
          class="btn-secondary ml-auto"
          :disabled="driverMissing || testState.kind === 'busy' || !credsFilled"
          @click="onTest"
        >
          {{ testState.kind === 'busy' ? 'Testing…' : 'Test connection' }}
        </button>
      </div>

      <!-- Row 7 (full width): connection status; wraps freely so long
           ODBC error strings don't clip off the right edge. -->
      <template v-if="testState.kind === 'ok' || testState.kind === 'err'">
        <div />
        <div
          class="col-span-4 whitespace-pre-wrap break-words font-mono text-2xs"
          :class="testState.kind === 'ok' ? 'text-success' : 'text-danger'"
        >
          <span v-if="testState.kind === 'ok'">✓ {{ testState.message || 'connected' }}</span>
          <span v-else>✗ {{ testState.message }}</span>
        </div>
      </template>
    </div>
  </section>
</template>
