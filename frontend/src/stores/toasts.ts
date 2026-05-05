// Tiny transient-notification store.
//
// Any part of the app can call `useToasts().show("message", "warn")` and
// a small item appears at the bottom-right of the window, fading out
// after a few seconds.  No queuing/batching magic; if several fire at
// once they stack.
//
// For long-running operations, `showSticky()` returns an id that the
// caller must `dismiss()` when the work finishes; no auto-timeout.

import { defineStore } from 'pinia'

export type ToastKind = 'info' | 'warn' | 'error' | 'success'

interface Toast {
  id: number
  message: string
  kind: ToastKind
}

let nextId = 1

export const useToasts = defineStore('toasts', {
  state: () => ({
    items: [] as Toast[],
  }),

  actions: {
    show(message: string, kind: ToastKind = 'info', ttlMs = 3500) {
      const id = nextId++
      this.items.push({ id, message, kind })
      window.setTimeout(() => {
        this.items = this.items.filter(t => t.id !== id)
      }, ttlMs)
    },

    showSticky(message: string, kind: ToastKind = 'info'): number {
      const id = nextId++
      this.items.push({ id, message, kind })
      return id
    },

    update(id: number, message: string, kind?: ToastKind) {
      const t = this.items.find(t => t.id === id)
      if (!t) return
      t.message = message
      if (kind) t.kind = kind
    },

    dismiss(id: number) {
      this.items = this.items.filter(t => t.id !== id)
    },
  },
})
