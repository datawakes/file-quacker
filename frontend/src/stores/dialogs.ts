// In-app prompt + confirm dialogs.  Replaces window.prompt /
// window.confirm so dialogs follow app theming, can be styled, and
// don't break dark mode.  Each call returns a Promise that resolves
// when the user accepts or dismisses.

import { defineStore } from 'pinia'

export interface PromptOptions {
  title: string
  /** Optional secondary line under the title. */
  message?: string
  /** Pre-filled value; user can edit or clear. */
  defaultValue?: string
  placeholder?: string
  okLabel?: string
  cancelLabel?: string
}

export interface ConfirmOptions {
  title: string
  message?: string
  okLabel?: string
  cancelLabel?: string
  /** Style the OK button as destructive (red). */
  danger?: boolean
}

type PromptResolver = (value: string | null) => void
type ConfirmResolver = (value: boolean) => void

interface ActivePrompt extends PromptOptions {
  kind: 'prompt'
  resolve: PromptResolver
}

interface ActiveConfirm extends ConfirmOptions {
  kind: 'confirm'
  resolve: ConfirmResolver
}

export type ActiveDialog = ActivePrompt | ActiveConfirm

interface DialogState {
  active: ActiveDialog | null
}

export const useDialogs = defineStore('dialogs', {
  state: (): DialogState => ({ active: null }),
  actions: {
    prompt(opts: PromptOptions): Promise<string | null> {
      return new Promise<string | null>((resolve) => {
        this.active = { kind: 'prompt', ...opts, resolve }
      })
    },
    confirm(opts: ConfirmOptions): Promise<boolean> {
      return new Promise<boolean>((resolve) => {
        this.active = { kind: 'confirm', ...opts, resolve }
      })
    },
    /** Resolve the active dialog with `value` and close it.  Safe to
     *  call when nothing is active (no-op). */
    resolve(value: string | boolean | null) {
      const a = this.active
      if (!a) return
      this.active = null
      if (a.kind === 'prompt') a.resolve(value as string | null)
      else a.resolve(Boolean(value))
    },
  },
})
