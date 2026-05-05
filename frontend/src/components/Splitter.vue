<script setup lang="ts">
// 1-px splitter.  Defaults to vertical (column resize); pass
// `orientation: 'horizontal'` for a row resize (editor / results split).
// The parent owns the size state; this component is a pure drag handle
// emitting `update:size`.
//
// `anchor`: which side of the splitter the controlled panel sits on.
// For sidebar-on-left style splits, the panel is on the `start` side
// (default); dragging RIGHT increases size.  For the profile drawer
// on the right, the panel is on the `end` side; dragging RIGHT must
// DECREASE the panel's width.  Passing `anchor: 'end'` inverts the
// delta accordingly.

import { computed, ref } from 'vue'

const props = defineProps<{
  size: number
  min?: number
  max?: number
  orientation?: 'vertical' | 'horizontal'
  anchor?: 'start' | 'end'
}>()

const emit = defineEmits<{
  (e: 'update:size', next: number): void
}>()

const dragging = ref(false)
const orient = computed(() => props.orientation ?? 'vertical')
const anchor = computed(() => props.anchor ?? 'start')

function onPointerDown(ev: PointerEvent) {
  ev.preventDefault()
  const startAxis = orient.value === 'vertical' ? ev.clientX : ev.clientY
  const startSize = props.size
  const min = props.min ?? 120
  const max = props.max ?? 2000
  dragging.value = true
  const el = ev.target as HTMLElement
  el.setPointerCapture?.(ev.pointerId)

  function onMove(e: PointerEvent) {
    const axis = orient.value === 'vertical' ? e.clientX : e.clientY
    const rawDelta = axis - startAxis
    const delta = anchor.value === 'end' ? -rawDelta : rawDelta
    const next = Math.max(min, Math.min(max, startSize + delta))
    emit('update:size', next)
  }
  function onUp(e: PointerEvent) {
    dragging.value = false
    el.releasePointerCapture?.(e.pointerId)
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
</script>

<template>
  <div
    class="shrink-0 bg-border hover:bg-border-strong"
    :class="[
      orient === 'vertical' ? 'w-px cursor-col-resize relative' : 'h-px cursor-row-resize relative',
      dragging ? 'bg-accent' : '',
    ]"
    :role="'separator'"
    :aria-orientation="orient === 'vertical' ? 'vertical' : 'horizontal'"
    @pointerdown="onPointerDown"
  >
    <div
      :class="orient === 'vertical' ? 'absolute inset-y-0 -left-1 w-3' : 'absolute inset-x-0 -top-1 h-3'"
    />
  </div>
</template>
