// Theme controller; three-state cycle (system / light / dark).
//
// Module-level singleton: every call to `useTheme()` returns the SAME
// reactive state so ThemeToggle, SqlEditor, and anyone else watching
// the theme all see the same flips.  Returning fresh refs per call (as
// the earlier version did) meant child components never observed the
// toggle button's changes.
//
// Default is `system`, which follows prefers-color-scheme.  The user's
// explicit choice (if any) is stored in localStorage under `fq.theme`.
// The `.dark` / `.light` class on <html> is kept in sync so Tailwind's
// darkMode-class strategy and our CSS variables pick up the palette.

import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

export type ThemeMode = 'system' | 'light' | 'dark'
export type EffectiveTheme = 'light' | 'dark'

interface ThemeApi {
  mode: Ref<ThemeMode>
  effective: ComputedRef<EffectiveTheme>
  cycle: () => void
}

const STORAGE_KEY = 'fq.theme'

function loadStored(): ThemeMode {
  const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'system'
}

function apply(t: EffectiveTheme) {
  const root = document.documentElement
  root.classList.toggle('dark', t === 'dark')
  root.classList.toggle('light', t === 'light')
  root.style.colorScheme = t
}

let singleton: ThemeApi | null = null

export function useTheme(): ThemeApi {
  if (singleton) return singleton

  const mode = ref<ThemeMode>(loadStored())
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  const systemPrefersDark = ref(mql.matches)

  mql.addEventListener('change', (e) => {
    systemPrefersDark.value = e.matches
  })

  const effective = computed<EffectiveTheme>(() => {
    if (mode.value === 'system') return systemPrefersDark.value ? 'dark' : 'light'
    return mode.value
  })

  apply(effective.value)
  watch(effective, apply)

  watch(mode, (m) => {
    try { localStorage.setItem(STORAGE_KEY, m) } catch { /* ignore */ }
  })

  function cycle() {
    mode.value = mode.value === 'system' ? 'light' : mode.value === 'light' ? 'dark' : 'system'
  }

  singleton = { mode, effective, cycle }
  return singleton
}
