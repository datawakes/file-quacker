// Trap keyboard focus inside a modal-dialog container.  Prevents Tab
// from escaping into the underlying grid; auto-focuses the first
// focusable element on mount and restores focus to the previously
// active element on unmount.

import { onBeforeUnmount, onMounted, type Ref } from 'vue'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function useFocusTrap(containerRef: Ref<HTMLElement | null>): void {
  let previouslyFocused: Element | null = null

  function focusables(): HTMLElement[] {
    const root = containerRef.value
    if (!root) return []
    return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
      .filter(el => el.offsetParent !== null || el === document.activeElement)
  }

  function onKey(e: KeyboardEvent) {
    if (e.key !== 'Tab') return
    const root = containerRef.value
    if (!root) return
    const els = focusables()
    if (els.length === 0) {
      e.preventDefault()
      return
    }
    const first = els[0]
    const last = els[els.length - 1]
    const active = document.activeElement as HTMLElement | null
    if (e.shiftKey && (active === first || !root.contains(active))) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && (active === last || !root.contains(active))) {
      e.preventDefault()
      first.focus()
    }
  }

  onMounted(() => {
    previouslyFocused = document.activeElement
    queueMicrotask(() => {
      const els = focusables()
      if (els.length) els[0].focus()
    })
    document.addEventListener('keydown', onKey, true)
  })

  onBeforeUnmount(() => {
    document.removeEventListener('keydown', onKey, true)
    if (previouslyFocused instanceof HTMLElement) {
      try { previouslyFocused.focus() } catch { /* ignore */ }
    }
  })
}
