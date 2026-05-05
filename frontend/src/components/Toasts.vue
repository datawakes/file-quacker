<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-vue-next'
import { useToasts } from '../stores/toasts'

const { items } = storeToRefs(useToasts())

const iconFor = {
  info: Info,
  warn: AlertTriangle,
  error: XCircle,
  success: CheckCircle2,
} as const
</script>

<template>
  <div class="pointer-events-none fixed bottom-8 right-4 z-50 flex flex-col items-end gap-2"
    role="status" aria-live="polite" aria-atomic="false">
    <TransitionGroup name="toast" tag="div" class="flex flex-col items-end gap-2">
      <div
        v-for="t in items"
        :key="t.id"
        class="pointer-events-auto flex max-w-sm items-start gap-2 rounded-md border bg-surface-1 px-3 py-2 text-xs shadow-lg"
        :class="{
          'border-border text-ink':        t.kind === 'info',
          'border-warn/40 text-warn':      t.kind === 'warn',
          'border-danger/40 text-danger':  t.kind === 'error',
          'border-success/40 text-success':t.kind === 'success',
        }"
        role="status"
      >
        <component :is="iconFor[t.kind]" class="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span class="whitespace-pre-wrap break-words font-medium">{{ t.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-enter-active, .toast-leave-active {
  transition: opacity 180ms ease-out, transform 180ms ease-out;
}
.toast-enter-from, .toast-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>
