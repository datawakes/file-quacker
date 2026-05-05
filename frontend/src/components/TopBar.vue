<script setup lang="ts">
import { ref } from 'vue'
import { HelpCircle, Table2, Terminal } from 'lucide-vue-next'
import { storeToRefs } from 'pinia'
import ThemeToggle from './ThemeToggle.vue'
import HelpDialog from './HelpDialog.vue'
import { useViewStore } from '../stores/view'
import { APP_VERSION } from '../lib/version'

const viewStore = useViewStore()
const { mode } = storeToRefs(viewStore)

const helpOpen = ref(false)

const jumpInput = ref('')

function onJump(e: Event) {
  e.preventDefault()
  const n = parseInt(jumpInput.value, 10)
  if (!Number.isFinite(n) || n < 1) return
  viewStore.jumpToRow(n)
}
</script>

<template>
  <header
    class="flex h-10 shrink-0 items-center justify-between border-b border-border bg-surface-0 pl-3 pr-2"
  >
    <div class="flex items-baseline gap-1.5">
      <span class="text-xs font-medium text-ink">File Quacker</span>
      <span class="text-2xs text-ink-subtle">v{{ APP_VERSION }}</span>
    </div>

    <!-- Mode switcher (Grid | SQL) -->
    <div
      class="flex items-center overflow-hidden rounded-md border border-border bg-surface-1 p-0.5 text-xs"
      role="tablist"
      aria-label="View"
    >
      <button
        type="button"
        role="tab"
        :aria-selected="mode === 'grid'"
        class="mode-btn"
        :class="{ 'mode-btn-on': mode === 'grid' }"
        @click="viewStore.setMode('grid')"
      >
        <Table2 class="h-3.5 w-3.5" />
        Grid
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="mode === 'sql'"
        class="mode-btn"
        :class="{ 'mode-btn-on': mode === 'sql' }"
        @click="viewStore.setMode('sql')"
      >
        <Terminal class="h-3.5 w-3.5" />
        SQL
      </button>
    </div>

    <div class="flex items-center gap-2">
      <form class="flex items-center gap-1.5" @submit="onJump">
        <label class="text-2xs text-ink-subtle" for="top-jump">Jump to row</label>
        <input
          id="top-jump"
          v-model="jumpInput"
          type="text"
          inputmode="numeric"
          pattern="[0-9]*"
          placeholder="#"
          class="h-6 w-20 rounded border border-border bg-surface-1 px-2 font-mono text-xs tabular-nums outline-none focus:border-accent"
        />
      </form>
      <ThemeToggle />
      <button
        type="button"
        class="btn-icon"
        aria-label="Help"
        title="Help"
        @click="helpOpen = true"
      >
        <HelpCircle class="h-4 w-4" />
      </button>
    </div>
  </header>

  <HelpDialog v-if="helpOpen" @close="helpOpen = false" />
</template>

<style scoped>
.mode-btn {
  @apply inline-flex items-center gap-1.5 rounded px-2.5 py-1 font-medium text-ink-muted transition-colors hover:text-ink;
}
.mode-btn-on {
  @apply bg-surface-0 text-ink shadow-sm;
}
</style>
