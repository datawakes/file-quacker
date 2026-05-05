<script setup lang="ts">
import { computed } from 'vue'
import { Monitor, Sun, Moon } from 'lucide-vue-next'
import { useTheme } from '../composables/useTheme'

const { mode, cycle } = useTheme()

const icon = computed(() => (mode.value === 'system' ? Monitor : mode.value === 'light' ? Sun : Moon))
const label = computed(() => (mode.value === 'system' ? 'System theme' : mode.value === 'light' ? 'Light theme' : 'Dark theme'))
</script>

<template>
  <button
    type="button"
    class="btn-icon"
    :title="label + ' (click to change)'"
    :aria-label="label"
    @click="cycle"
  >
    <component :is="icon" class="h-4 w-4" />
  </button>
</template>
