<script setup lang="ts">
// Right-side panel that shows the full multi-strategy ingest failure
// message for a file that couldn't be loaded.  The body is selectable
// plain text; a Copy button grabs the whole message.

import { Copy, X } from 'lucide-vue-next'
import { useToasts } from '../stores/toasts'

const props = defineProps<{
  path: string
  fileName: string
  message: string
}>()

defineEmits<{ (e: 'close'): void }>()

const toasts = useToasts()

async function onCopy() {
  try {
    await navigator.clipboard.writeText(
      `File: ${props.path}\n\n${props.message}`,
    )
    toasts.show('Error copied to clipboard', 'success', 2000)
  } catch {
    toasts.show('Copy failed', 'error')
  }
}

function onPanelCopy(e: ClipboardEvent) {
  const sel = window.getSelection()
  if (!sel || !sel.toString()) return
  e.preventDefault()
  e.clipboardData?.setData('text/plain', sel.toString())
}
</script>

<template>
  <aside
    class="flex h-full flex-col bg-surface-0"
    aria-label="Ingest error"
    @copy="onPanelCopy"
  >
    <div class="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
      <div class="flex min-w-0 flex-1 items-baseline gap-2">
        <span class="text-sm text-danger">Couldn't open file</span>
        <span class="truncate font-mono text-2xs text-ink-subtle" :title="path">{{ fileName }}</span>
      </div>
      <button class="btn-icon" title="Copy error" aria-label="Copy error" @click="onCopy">
        <Copy class="h-4 w-4" />
      </button>
      <button class="btn-icon" title="Close" aria-label="Close" @click="$emit('close')">
        <X class="h-4 w-4" />
      </button>
    </div>

    <div class="min-h-0 flex-1 overflow-auto px-3 py-3">
      <div class="mb-3 font-mono text-2xs text-ink-subtle break-all" :title="path">{{ path }}</div>
      <pre class="m-0 select-text whitespace-pre-wrap break-words font-mono text-xs leading-5 text-ink">{{ message }}</pre>
    </div>
  </aside>
</template>
