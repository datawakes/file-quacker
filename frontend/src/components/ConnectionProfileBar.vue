<script setup lang="ts">
// Shared, driver-agnostic saved-connection bar.
//
// Sits at the top of any driver's connection form (Import + Export).
// One-time use is the DEFAULT and costs nothing: if the user never
// touches this bar, the form behaves exactly as before.  Saving is an
// explicit, inline, opt-in action — no modal gate, no post-connect nag.
//
// Secrets never round-trip through here: selecting a profile clears the
// secret fields and the backend resolves them from the OS vault via the
// `profileId` the host passes to its test/import/export calls.

import { computed, onMounted, ref, watch } from 'vue'
import { Save, Trash2 } from 'lucide-vue-next'
import {
  deleteConnectionProfile,
  getConnectionProfile,
  listConnectionProfiles,
  saveConnectionProfile,
  type ConnectionProfile,
} from '../lib/api'
import { useToasts } from '../stores/toasts'

const props = withDefaults(defineProps<{
  driverId: string
  /** The reactive connection object; mutated in place on profile select. */
  connOpts: Record<string, unknown>
  /** Field keys stored in the vault (from the driver's form). */
  secretKeys?: string[]
  /** Width of the leading "Profile" label, so the select lines up with
   *  the first field of the host form (110px import grid, 80px export). */
  labelWidth?: string
  /** When set, the select + Save share this width (matching the host's
   *  text inputs) so Save right-aligns with them and the select fills the
   *  rest.  Unset → natural width (export). */
  fieldWidth?: string
}>(), { secretKeys: () => ['password'], labelWidth: '110px' })

const fieldStyle = computed(() => (props.fieldWidth ? { width: props.fieldWidth } : {}))

const profileId = defineModel<string | null>('profileId', { default: null })
// Char count per saved secret field for the active profile, so the host
// can render masked placeholder dots of the right length in its
// (intentionally blank) password input.  Empty when no profile / no secret.
const secretLengths = defineModel<Record<string, number>>('secretLengths', { default: () => ({}) })

const toasts = useToasts()
const profiles = ref<ConnectionProfile[]>([])

const selected = computed(() => profiles.value.find(p => p.id === profileId.value) ?? null)
const hasSecretValue = computed(() =>
  props.secretKeys.some(k => {
    const v = props.connOpts[k]
    return v !== undefined && v !== null && v !== ''
  }),
)

async function loadProfiles() {
  try {
    profiles.value = await listConnectionProfiles(props.driverId)
  } catch {
    profiles.value = []
  }
}

onMounted(loadProfiles)
// Re-list when the driver changes; drop any selection that no longer applies.
watch(() => props.driverId, () => { profileId.value = null; secretLengths.value = {}; void loadProfiles() })

function clearSecretFields() {
  for (const k of props.secretKeys) {
    if (k in props.connOpts) props.connOpts[k] = ''
  }
}

// --- select / clear ------------------------------------------------------ //
const selectValue = computed<string>({
  get: () => profileId.value ?? '',
  set: (v) => { if (v) void selectProfile(v); else { profileId.value = null; secretLengths.value = {} } },
})

async function selectProfile(id: string) {
  try {
    const detail = await getConnectionProfile(id)
    if (!detail) { toasts.show('Profile not found', 'warn'); await loadProfiles(); return }
    // Populate non-secret fields, then blank the secrets (resolved backend-side).
    for (const [k, v] of Object.entries(detail.conn_opts)) props.connOpts[k] = v as unknown
    clearSecretFields()
    secretLengths.value = detail.secret_lengths ?? {}
    profileId.value = id
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  }
}

// --- save ---------------------------------------------------------------- //
const showSave = ref(false)
const saveName = ref('')
const savePassword = ref(true)

function openSave() {
  saveName.value = selected.value?.name ?? ''
  // Default to keeping the password when one is already on file (the field
  // is blank because it shows a masked placeholder) or the user typed one.
  savePassword.value = hasSecretValue.value || Object.keys(secretLengths.value).length > 0
  showSave.value = true
}

async function confirmSave() {
  const name = saveName.value.trim()
  if (!name) return
  try {
    const res = await saveConnectionProfile(name, props.driverId, { ...props.connOpts }, savePassword.value)
    if (res.error) { toasts.show(res.error, 'error', 6000); return }
    await loadProfiles()
    if (res.id) profileId.value = res.id
    // Authoritative lengths from the save (reflects any preserved secret).
    secretLengths.value = res.secret_lengths ?? {}
    clearSecretFields()
    showSave.value = false
    toasts.show(`Saved profile “${name}”`, 'success')
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 6000)
  }
}

// --- delete -------------------------------------------------------------- //
const confirmingDelete = ref(false)

async function confirmDelete() {
  const id = profileId.value
  if (!id) return
  const name = selected.value?.name ?? id
  try {
    await deleteConnectionProfile(id)
    profileId.value = null
    secretLengths.value = {}
    confirmingDelete.value = false
    await loadProfiles()
    toasts.show(`Deleted profile “${name}”`, 'info')
  } catch (e) {
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 6000)
  }
}
</script>

<template>
  <div class="relative mb-2 flex items-center gap-3 text-xs">
    <label class="shrink-0 text-ink-muted" :style="{ width: labelWidth }">Profile</label>
    <div class="flex items-center gap-2" :style="fieldStyle">
      <select
        v-model="selectValue"
        :class="fieldWidth ? 'flex-1 min-w-0' : 'w-56'"
        class="h-7 rounded border border-border bg-surface-0 px-2 outline-none focus:border-accent"
      >
        <option value="">- none (one-time use) -</option>
        <option v-for="p in profiles" :key="p.id" :value="p.id">
          {{ p.name }}{{ p.has_secret ? ' 🔑' : '' }}
        </option>
      </select>
      <button
        v-if="profileId"
        type="button"
        class="btn-icon shrink-0"
        title="Delete profile"
        aria-label="Delete profile"
        @click="confirmingDelete = true"
      >
        <Trash2 class="h-3.5 w-3.5" />
      </button>
      <button type="button" class="btn-secondary shrink-0" @click="openSave">
        <Save class="h-3 w-3" /> Save
      </button>
    </div>

    <!-- Inline delete confirm -->
    <div
      v-if="confirmingDelete"
      class="absolute left-0 top-8 z-10 flex items-center gap-2 rounded border border-border bg-surface-1 px-3 py-2 shadow-lg"
    >
      <span>Delete “{{ selected?.name }}”?</span>
      <button type="button" class="btn-secondary" @click="confirmDelete">Delete</button>
      <button type="button" class="btn-secondary" @click="confirmingDelete = false">Cancel</button>
    </div>

    <!-- Inline save popover -->
    <div
      v-if="showSave"
      class="absolute left-0 top-8 z-10 flex w-80 flex-col gap-2 rounded border border-border bg-surface-1 p-3 shadow-lg"
    >
      <label class="text-ink-muted">Profile name</label>
      <input
        v-model="saveName"
        type="text"
        placeholder="e.g. Prod SQL"
        class="h-7 rounded border border-border bg-surface-0 px-2 outline-none focus:border-accent"
        @keydown.enter="confirmSave"
      />
      <label v-if="secretKeys.length" class="flex items-center gap-1.5">
        <input v-model="savePassword" type="checkbox" />
        <span>Save password securely</span>
      </label>
      <p v-if="secretKeys.length" class="text-2xs text-ink-subtle">
        Stored in your account's Windows Credential Manager.
      </p>
      <div class="flex items-center justify-end gap-2">
        <button type="button" class="btn-secondary" @click="showSave = false">Cancel</button>
        <button type="button" class="btn-primary" :disabled="!saveName.trim()" @click="confirmSave">Save</button>
      </div>
    </div>
  </div>
</template>
