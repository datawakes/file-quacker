// Polls the shared column-inspection counter.  Used by ExportDialog,
// DdlPanel, and DeriveDialog so the label stays consistent.  Call start()
// before the slow backend call, stop() in its finally.

import { computed, onBeforeUnmount, ref } from 'vue'
import { getInspectProgress, type InspectProgress } from '../lib/api'

export function useInspectProgress() {
  const inspect = ref<InspectProgress | null>(null)
  let timer: number | null = null
  let generation = 0
  let mounted = true

  function start() {
    if (timer != null) window.clearInterval(timer)
    inspect.value = null
    generation += 1
    const myGen = generation
    timer = window.setInterval(async () => {
      try {
        const p = await getInspectProgress()
        // A poll can resolve after stop() — or after a later start() —
        // and would otherwise overwrite fresh state with a stale snapshot.
        if (mounted && myGen === generation && timer != null) inspect.value = p
      } catch { /* ignore */ }
    }, 250)
  }

  function stop() {
    if (timer != null) { window.clearInterval(timer); timer = null }
    inspect.value = null
  }

  const label = computed(() => {
    const p = inspect.value
    if (!p || p.total === 0) return 'Analyzing columns…'
    return `Analyzing columns (${p.current} of ${p.total})…`
  })

  onBeforeUnmount(() => {
    mounted = false
    if (timer != null) window.clearInterval(timer)
  })

  return { label, start, stop }
}
