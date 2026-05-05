<script setup lang="ts">
// Host for the in-app prompt + confirm dialogs driven by useDialogs().
// Mounted once at the root; renders nothing when no dialog is active.

import { nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useDialogs } from '../stores/dialogs'
import { useFocusTrap } from '../composables/useFocusTrap'

const dialogs = useDialogs()
const { active } = storeToRefs(dialogs)

const containerRef = ref<HTMLElement | null>(null)
useFocusTrap(containerRef)

const inputValue = ref('')
const inputRef = ref<HTMLInputElement | null>(null)

watch(active, async (a) => {
  if (a?.kind === 'prompt') {
    inputValue.value = a.defaultValue ?? ''
    await nextTick()
    inputRef.value?.focus()
    inputRef.value?.select()
  }
}, { immediate: true })

function onCancel() {
  if (!active.value) return
  dialogs.resolve(active.value.kind === 'prompt' ? null : false)
}

function onOk() {
  if (!active.value) return
  if (active.value.kind === 'prompt') {
    const v = inputValue.value.trim()
    dialogs.resolve(v === '' ? null : v)
  } else {
    dialogs.resolve(true)
  }
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.preventDefault(); onCancel() }
  if (e.key === 'Enter' && active.value?.kind === 'confirm') { e.preventDefault(); onOk() }
}
</script>

<template>
  <div
    v-if="active"
    ref="containerRef"
    class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-[1px]"
    role="dialog"
    aria-modal="true"
    @keydown="onKey"
  >
    <div class="flex w-full max-w-md flex-col rounded-md border border-border bg-surface-1 shadow-xl">
      <div class="flex flex-col gap-1 border-b border-border px-4 py-3">
        <h2 class="text-sm font-medium text-ink">{{ active.title }}</h2>
        <p v-if="active.message" class="text-xs text-ink-muted">{{ active.message }}</p>
      </div>

      <div v-if="active.kind === 'prompt'" class="px-4 py-3">
        <form @submit.prevent="onOk">
          <input
            ref="inputRef"
            v-model="inputValue"
            type="text"
            :placeholder="active.placeholder"
            class="h-8 w-full rounded border border-border bg-surface-0 px-2 font-mono text-xs outline-none focus:border-accent"
          />
        </form>
      </div>

      <div class="flex items-center justify-end gap-2 border-t border-border px-4 py-2.5">
        <button type="button" class="btn-secondary" @click="onCancel">
          {{ active.cancelLabel ?? 'Cancel' }}
        </button>
        <button
          type="button"
          :class="active.kind === 'confirm' && active.danger ? 'btn-danger' : 'btn-yellow'"
          @click="onOk"
        >
          {{ active.okLabel ?? 'OK' }}
        </button>
      </div>
    </div>
  </div>
</template>
