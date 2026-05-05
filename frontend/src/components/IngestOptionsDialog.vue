<script setup lang="ts">
// Open-with-options / Reload-with-options dialog.
//
// Shows the first 30 raw lines so the user can click the real header
// row (when there's a preamble) and override delimiter / encoding
// before ingest.  Reachable from the sidebar's Open chevron and from
// each table's "Reload with options…" entry.

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { X, Play } from 'lucide-vue-next'
import { previewFileLines, type IngestOptions } from '../lib/api'
import { useTablesStore } from '../stores/tables'
import { useToasts } from '../stores/toasts'
import { useFocusTrap } from '../composables/useFocusTrap'

const dialogRef = ref<HTMLElement | null>(null)
useFocusTrap(dialogRef)

const props = defineProps<{
  path: string
  /** When set, drop this workspace table after the new load succeeds
   *  and reuse its name for the new table. */
  reloadTable?: string | null
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

const tables = useTablesStore()
const toasts = useToasts()

const fileName = computed(() => props.path.split(/[\\/]/).pop() || props.path)

const PREVIEW_N = 30

// Preview state
const lines = ref<string[]>([])
const detectedEncoding = ref('utf-8')
const sniffedDelimiter = ref<string>(',')
const previewLoading = ref(true)
const previewError = ref<string | null>(null)

// Form state
const firstRow = ref(1)                 // 1-based row number that holds the header
const skipRows = ref(0)
const delimiterChoice = ref<'auto' | ',' | '|' | ';' | '\t' | 'custom'>('auto')
const customDelimiter = ref('')
const encodingChoice = ref<'auto' | 'utf-8' | 'cp1252' | 'utf-16' | 'utf-16le' | 'utf-16be' | 'latin-1'>('auto')
const quote = ref<string>('"')
const ignoreErrors = ref(false)
const busy = ref(false)

async function loadPreview(forEncoding?: string | null) {
  previewLoading.value = true
  previewError.value = null
  try {
    const r = await previewFileLines(props.path, PREVIEW_N, forEncoding ?? null)
    lines.value = r.lines
    detectedEncoding.value = r.encoding
    sniffedDelimiter.value = r.sniffed_delimiter || ','
  } catch (e) {
    previewError.value = e instanceof Error ? e.message : String(e)
    lines.value = []
  } finally {
    previewLoading.value = false
  }
}

onMounted(() => { void loadPreview() })

// Re-decode the preview when the user picks a different encoding so
// mojibake doesn't linger after they switch.
watch(encodingChoice, (v) => {
  if (v === 'auto') void loadPreview()
  else void loadPreview(v)
})

function selectHeaderRow(idx: number) {
  firstRow.value = idx + 1
}

function effectiveDelimiter(): string | null {
  if (delimiterChoice.value === 'auto') return null
  if (delimiterChoice.value === 'custom') return customDelimiter.value || null
  return delimiterChoice.value
}

function effectiveEncoding(): string | null {
  return encodingChoice.value === 'auto' ? null : encodingChoice.value
}

function buildOpts(): IngestOptions {
  const opts: IngestOptions = {
    first_row: firstRow.value,
    skip_rows: skipRows.value,
    quote: quote.value,
    ignore_errors: ignoreErrors.value,
  }
  const d = effectiveDelimiter()
  if (d) opts.delimiter = d
  const e = effectiveEncoding()
  if (e) opts.encoding = e
  if (props.reloadTable) opts.preferred_table_name = props.reloadTable
  // strict_mode is owned by the ladder, not exposed via IngestOptions.
  return opts
}

async function onConfirm() {
  if (busy.value) return
  busy.value = true
  try {
    // Drop first so reloadTable's name is free for preferred_table_name
    // (otherwise _unique_name suffixes the new table).
    if (props.reloadTable) {
      await tables.dropByName(props.reloadTable)
    }
    await tables.loadPath(props.path, buildOpts())
    emit('close')
  } catch (e) {
    // loadPath opens its own IngestErrorPanel; the toast is just a hint.
    toasts.show(e instanceof Error ? e.message : String(e), 'error', 5000)
  } finally {
    busy.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    const t = e.target as HTMLElement | null
    const tag = t?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t?.isContentEditable) {
      (t as HTMLElement).blur()
      return
    }
    emit('close')
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

// Color the chosen delimiter inside preview lines so the field
// structure is obvious before the user clicks Open.
const highlightChar = computed(() => {
  if (delimiterChoice.value === 'auto') return sniffedDelimiter.value
  if (delimiterChoice.value === 'custom') return customDelimiter.value
  return delimiterChoice.value
})

function splitOnDelim(line: string): { text: string; isDelim: boolean }[] {
  const ch = highlightChar.value
  if (!ch) return [{ text: line, isDelim: false }]
  const parts: { text: string; isDelim: boolean }[] = []
  let buf = ''
  for (const c of line) {
    if (c === ch) {
      if (buf) { parts.push({ text: buf, isDelim: false }); buf = '' }
      parts.push({ text: c, isDelim: true })
    } else {
      buf += c
    }
  }
  if (buf) parts.push({ text: buf, isDelim: false })
  return parts
}
</script>

<template>
  <div
    class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-[1px]"
  >
    <div
      ref="dialogRef"
      class="flex flex-col rounded-md border border-border bg-surface-1 shadow-xl"
      style="height: min(720px, 90vh); width: min(1100px, 95vw);"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ingest-opts-title"
    >
      <!-- Header -->
      <div class="flex h-10 shrink-0 items-center gap-3 border-b border-border px-3">
        <h2 id="ingest-opts-title" class="text-sm font-medium text-ink">
          {{ reloadTable ? 'Reload with options' : 'Open with options' }}
        </h2>
        <span class="truncate font-mono text-2xs text-ink-subtle" :title="path">{{ fileName }}</span>
        <button type="button" class="btn-icon ml-auto" aria-label="Close" title="Close" @click="$emit('close')">
          <X class="h-4 w-4" />
        </button>
      </div>

      <!-- Body: preview | options -->
      <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_300px] divide-x divide-border">
        <!-- Preview -->
        <div class="flex min-h-0 flex-col">
          <div class="flex shrink-0 items-center justify-between border-b border-border px-3 py-1.5">
            <h3 class="text-2xs uppercase tracking-wide text-ink-subtle">
              Preview — first {{ PREVIEW_N }} lines
            </h3>
            <span class="font-mono text-2xs text-ink-subtle" :title="`Encoding used to decode: ${detectedEncoding}`">
              {{ detectedEncoding }}
            </span>
          </div>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-if="previewLoading" class="px-3 py-3 text-xs text-ink-subtle">Reading file…</div>
            <div v-else-if="previewError" class="px-3 py-3 font-mono text-xs text-danger whitespace-pre-wrap">{{ previewError }}</div>
            <table v-else class="w-full border-collapse">
              <tbody>
                <tr
                  v-for="(line, idx) in lines"
                  :key="idx"
                  class="cursor-pointer border-b border-border/40 align-top hover:bg-surface-2/60"
                  :class="firstRow === idx + 1 ? 'bg-accent/10' : ''"
                  :title="firstRow === idx + 1 ? 'Header row' : 'Click to mark as header'"
                  @click="selectHeaderRow(idx)"
                >
                  <td class="select-none border-r border-border/40 px-2 py-1 text-right font-mono text-2xs tabular-nums text-ink-subtle" style="width: 40px">
                    {{ idx + 1 }}
                  </td>
                  <td class="select-none px-2 py-1 font-mono text-2xs text-ink whitespace-pre overflow-hidden">
                    <span
                      v-for="(part, partIdx) in splitOnDelim(line)"
                      :key="partIdx"
                      :class="part.isDelim ? 'text-accent font-bold' : ''"
                    >{{ part.text }}</span>
                  </td>
                </tr>
                <tr v-if="!lines.length">
                  <td colspan="2" class="px-3 py-3 text-center text-xs text-ink-subtle">No lines to preview.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Options -->
        <div class="flex min-h-0 flex-col gap-2 overflow-y-auto px-3 py-3 text-xs">
          <div>
            <label class="mb-1 block text-ink-muted">Header row</label>
            <input
              v-model.number="firstRow"
              type="number"
              min="1"
              :max="PREVIEW_N"
              class="h-7 w-full rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
            />
            <p class="mt-1 text-2xs text-ink-subtle">Click a row in the preview to set this.</p>
          </div>

          <div>
            <label class="mb-1 block text-ink-muted">Delimiter</label>
            <select
              v-model="delimiterChoice"
              class="h-7 w-full rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
            >
              <option value="auto">Auto (sniffed: {{ sniffedDelimiter === '\t' ? '\\t' : sniffedDelimiter }})</option>
              <option value=",">Comma ,</option>
              <option value="|">Pipe |</option>
              <option value=";">Semicolon ;</option>
              <option value="	">Tab \t</option>
              <option value="custom">Custom…</option>
            </select>
            <input
              v-if="delimiterChoice === 'custom'"
              v-model="customDelimiter"
              type="text"
              placeholder="single character"
              maxlength="1"
              class="mt-1 h-7 w-full rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
            />
          </div>

          <div>
            <label class="mb-1 block text-ink-muted">Encoding</label>
            <select
              v-model="encodingChoice"
              class="h-7 w-full rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
            >
              <option value="auto">Auto (detected: {{ detectedEncoding }})</option>
              <option value="utf-8">UTF-8</option>
              <option value="cp1252">Windows-1252 (cp1252)</option>
              <option value="utf-16">UTF-16</option>
              <option value="utf-16le">UTF-16 LE</option>
              <option value="utf-16be">UTF-16 BE</option>
              <option value="latin-1">Latin-1 / ISO-8859-1</option>
            </select>
          </div>

          <div>
            <label class="mb-1 block text-ink-muted">Quote character</label>
            <input
              v-model="quote"
              type="text"
              maxlength="1"
              placeholder='" (none = unquoted)'
              class="h-7 w-full rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
            />
          </div>

          <label class="flex items-center gap-2 text-ink-muted">
            <input v-model="ignoreErrors" type="checkbox" />
            Skip rows that don't parse cleanly
          </label>

          <div class="mt-auto pt-2 text-2xs text-ink-subtle">
            <p>If you leave Delimiter / Encoding on Auto, the normal ingest ladder runs.  Setting them here forces the chosen value through every fallback rung.</p>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="flex h-11 shrink-0 items-center justify-end gap-2 border-t border-border px-3">
        <button type="button" class="btn-secondary" :disabled="busy" @click="$emit('close')">
          Cancel
        </button>
        <button
          type="button"
          class="btn-primary"
          :disabled="busy || previewLoading"
          @click="onConfirm"
        >
          <Play class="h-3 w-3" />
          {{ reloadTable ? 'Reload' : 'Open' }}
        </button>
      </div>
    </div>
  </div>
</template>
