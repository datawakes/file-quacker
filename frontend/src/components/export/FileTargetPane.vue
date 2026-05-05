<script setup lang="ts">
import { computed } from 'vue'
import type { FileTarget } from '../../lib/api'
import { saveFileDialog } from '../../lib/api'

type FlatT = Extract<FileTarget, { kind: 'flat' }>

const DELIMITER_PRESETS = [
  { label: 'Pipe (|)',      value: '|'  },
  { label: 'Comma (,)',     value: ','  },
  { label: 'Tab',           value: '\t' },
  { label: 'Semicolon (;)', value: ';'  },
]

const props = defineProps<{
  target: FileTarget
  driverMissing: boolean
}>()
const emit = defineEmits<{ (e: 'update:target', t: FileTarget): void }>()

const t = computed({
  get: () => props.target,
  set: (v: FileTarget) => emit('update:target', v),
})

function onPath(e: Event) {
  const p = (e.target as HTMLInputElement).value
  t.value = { ...t.value, path: p }
}

function flatSet<K extends keyof FlatT>(key: K, value: FlatT[K]) {
  if (t.value.kind !== 'flat') return
  t.value = { ...t.value, [key]: value }
}

const flatDelimiter = computed(() => t.value.kind === 'flat' ? t.value.delimiter : '')
const isCustomDelimiter = computed(() => {
  const d = flatDelimiter.value
  return t.value.kind === 'flat' && !DELIMITER_PRESETS.some(p => p.value === d)
})

async function onBrowse() {
  const ext = t.value.kind === 'parquet' ? 'parquet'
            : t.value.kind === 'excel' ? 'xlsx'
            : 'txt'
  const def = `export.${ext}`
  const filter = ext === 'parquet' ? 'Parquet (*.parquet)'
               : ext === 'xlsx'    ? 'Excel (*.xlsx)'
               :                     'Flat file (*.txt;*.csv;*.tsv;*.dat)'
  const p = await saveFileDialog(def, [filter, 'All files (*.*)'])
  if (p) t.value = { ...t.value, path: p }
}
</script>

<template>
  <section class="shrink-0 border-b border-border p-3">
    <div
      class="grid gap-x-3 gap-y-1.5 text-xs"
      style="grid-template-columns: 80px minmax(0, 1fr);"
    >
      <label class="self-center text-ink-muted">Path</label>
      <div class="flex gap-2">
        <input
          :value="t.path"
          @input="onPath"
          type="text"
          :placeholder="t.kind === 'parquet' ? 'C:\\path\\to\\out.parquet'
                       : t.kind === 'excel' ? 'C:\\path\\to\\out.xlsx'
                       : 'C:\\path\\to\\out.txt'"
          class="h-7 flex-1 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent disabled:opacity-50"
        />
        <button type="button" class="btn-secondary" @click="onBrowse">
          Browse…
        </button>
      </div>

      <template v-if="t.kind === 'flat'">
        <label class="self-center text-ink-muted">Delimiter</label>
        <div class="flex items-center gap-2">
          <select
            :value="t.delimiter"
            @change="flatSet('delimiter', ($event.target as HTMLSelectElement).value)"
            class="h-7 rounded border border-border bg-surface-0 px-2 font-mono text-xs outline-none focus:border-accent"
          >
            <option v-for="p in DELIMITER_PRESETS" :key="p.value" :value="p.value">{{ p.label }}</option>
            <option
              v-if="isCustomDelimiter"
              :value="flatDelimiter"
            >Custom ({{ flatDelimiter || '∅' }})</option>
          </select>
          <span class="text-ink-subtle">or</span>
          <input
            :value="t.delimiter"
            @input="flatSet('delimiter', ($event.target as HTMLInputElement).value)"
            type="text"
            maxlength="2"
            placeholder="|"
            class="h-7 w-16 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
          />
        </div>

        <label class="self-center text-ink-muted">Header</label>
        <label class="self-center">
          <input type="checkbox" :checked="t.header"
            @change="flatSet('header', ($event.target as HTMLInputElement).checked)" />
          <span class="ml-1.5">Write column names on first row</span>
        </label>

        <label class="self-center text-ink-muted">Quote</label>
        <input
          :value="t.quote"
          @input="flatSet('quote', ($event.target as HTMLInputElement).value)"
          type="text"
          maxlength="1"
          class="h-7 w-16 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
        />

        <label class="self-center text-ink-muted">NULL as</label>
        <input
          :value="t.null"
          @input="flatSet('null', ($event.target as HTMLInputElement).value)"
          type="text"
          class="h-7 w-40 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
        />

        <label class="self-center text-ink-muted">Line ending</label>
        <select
          :value="t.line_ending"
          @change="flatSet('line_ending', ($event.target as HTMLSelectElement).value as 'crlf' | 'lf')"
          class="h-7 w-40 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
        >
          <option value="crlf">CRLF (Windows)</option>
          <option value="lf">LF (Unix)</option>
        </select>
      </template>

      <template v-if="t.kind === 'parquet'">
        <label class="self-center text-ink-muted">Compression</label>
        <select
          :value="t.compression"
          @change="t = { ...t, compression: ($event.target as HTMLSelectElement).value as 'snappy'|'zstd'|'gzip'|'uncompressed' }"
          class="h-7 w-40 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
        >
          <option value="snappy">snappy</option>
          <option value="zstd">zstd</option>
          <option value="gzip">gzip</option>
          <option value="uncompressed">uncompressed</option>
        </select>
      </template>

      <template v-if="t.kind === 'excel'">
        <label class="self-center text-ink-muted">Sheet</label>
        <input
          :value="t.sheet_name"
          @input="t = { ...t, sheet_name: ($event.target as HTMLInputElement).value }"
          type="text"
          class="h-7 w-56 rounded border border-border bg-surface-0 px-2 font-mono outline-none focus:border-accent"
        />
      </template>
    </div>
  </section>
</template>
