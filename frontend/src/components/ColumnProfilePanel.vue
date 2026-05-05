<script setup lang="ts">
// Right-side drawer; column stats for a table column or a SQL result
// column.  Re-fetches only when the (source, column) pair changes.

import { computed, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { profileColumn, profileQueryColumn, type ColumnProfile } from '../lib/api'
import type { ProfileSource } from '../stores/view'

const props = defineProps<{
  source: ProfileSource
  column: string
}>()

defineEmits<{ (e: 'close'): void }>()

const profile = ref<ColumnProfile | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    profile.value = props.source.kind === 'table'
      ? await profileColumn(props.source.name, props.column)
      : await profileQueryColumn(props.source.sql, props.column)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

watch(
  () => [
    props.source.kind === 'table' ? `t:${props.source.name}` : `q:${props.source.sql}`,
    props.column,
  ] as const,
  load,
  { immediate: true },
)

function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits })
}
function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return v.toFixed(2) + '%'
}

interface VarcharBranch {
  min_length: number | null
  max_length: number | null
  min_length_first_row: number | null
  max_length_first_row: number | null
  empty_string_count: number
  whitespace_only_count: number
  leading_or_trailing_space_count: number
  contains_space_count: number
  email_match_count: number
  type_sniff: {
    date_pct: number
    timestamp_pct: number
    integer_pct: number
    double_pct: number
    boolean_pct: number
  }
}
interface NumericBranch {
  zero_count: number
  negative_count: number
  mean: number | null
  median: number | null
  stddev: number | null
  q1: number | null
  q3: number | null
  iqr: number | null
  outlier_count: number | null
}
interface TemporalBranch {
  future_count: number
  unique_years: number
  earliest: string | null
  latest: string | null
  range_days: number | null
}

const varcharBranch = computed<VarcharBranch | null>(() =>
  profile.value?.kind === 'varchar' ? (profile.value.branch as unknown as VarcharBranch) : null,
)
const numericBranch = computed<NumericBranch | null>(() =>
  profile.value?.kind === 'numeric' ? (profile.value.branch as unknown as NumericBranch) : null,
)
const temporalBranch = computed<TemporalBranch | null>(() =>
  profile.value?.kind === 'temporal' ? (profile.value.branch as unknown as TemporalBranch) : null,
)
</script>

<template>
  <aside
    class="flex h-full flex-col bg-surface-0"
    aria-label="Column profile"
  >
    <!-- Header -->
    <div class="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
      <div class="flex min-w-0 flex-1 items-baseline gap-2">
        <span class="truncate font-mono text-sm text-ink" :title="column">{{ column }}</span>
        <span v-if="profile" class="font-mono text-2xs text-ink-subtle">{{ profile.type }}</span>
      </div>
      <button class="btn-icon" aria-label="Close profile" title="Close" @click="$emit('close')">
        <X class="h-4 w-4" />
      </button>
    </div>

    <!-- Body -->
    <div class="min-h-0 flex-1 overflow-y-auto px-3 py-3 text-xs">
      <div v-if="loading" class="text-ink-subtle">Loading…</div>
      <div v-else-if="error" class="font-mono text-danger whitespace-pre-wrap">{{ error }}</div>
      <template v-else-if="profile">
        <section class="mb-4">
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-wide text-ink-subtle">Overview</h3>
          <dl class="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono tabular-nums">
            <dt class="text-ink-muted">column #</dt><dd class="text-ink">{{ profile.column_position }}</dd>
            <dt class="text-ink-muted">row count</dt><dd class="text-ink">{{ fmtNum(profile.common.row_count) }}</dd>
            <dt class="text-ink-muted">distinct</dt><dd class="text-ink">{{ fmtNum(profile.common.distinct_count) }}</dd>
            <dt class="text-ink-muted">nulls</dt><dd class="text-ink">{{ fmtNum(profile.common.null_count) }}</dd>
            <dt class="text-ink-muted">fill</dt><dd class="text-ink">{{ fmtPct(profile.common.fill_pct) }}</dd>
            <dt class="text-ink-muted">cardinality</dt><dd class="text-ink">{{ fmtPct(profile.common.cardinality_pct) }}</dd>
            <dt class="text-ink-muted">min</dt><dd class="truncate text-ink" :title="profile.common.min ?? undefined">{{ profile.common.min ?? '—' }}</dd>
            <dt class="text-ink-muted">max</dt><dd class="truncate text-ink" :title="profile.common.max ?? undefined">{{ profile.common.max ?? '—' }}</dd>
          </dl>
        </section>

        <section v-if="profile.common.top_values.length" class="mb-4">
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-wide text-ink-subtle">Top 3 values</h3>
          <ul class="space-y-1">
            <li
              v-for="(tv, i) in profile.common.top_values"
              :key="i"
              class="flex items-baseline gap-2 font-mono"
            >
              <span class="truncate text-ink" :title="tv.value ?? undefined">{{ tv.value ?? 'NULL' }}</span>
              <span class="ml-auto shrink-0 tabular-nums text-ink-muted">
                {{ fmtNum(tv.count) }}
                <span class="text-ink-subtle">({{ tv.percent.toFixed(2) }}%)</span>
              </span>
            </li>
          </ul>
        </section>

        <section v-if="numericBranch" class="mb-4">
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-wide text-ink-subtle">Numeric</h3>
          <dl class="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono tabular-nums">
            <dt class="text-ink-muted">zeros</dt><dd class="text-ink">{{ fmtNum(numericBranch.zero_count) }}</dd>
            <dt class="text-ink-muted">negatives</dt><dd class="text-ink">{{ fmtNum(numericBranch.negative_count) }}</dd>
            <dt class="text-ink-muted">mean</dt><dd class="text-ink">{{ fmtNum(numericBranch.mean, 4) }}</dd>
            <dt class="text-ink-muted">median</dt><dd class="text-ink">{{ fmtNum(numericBranch.median, 4) }}</dd>
            <dt class="text-ink-muted">stddev</dt><dd class="text-ink">{{ fmtNum(numericBranch.stddev, 4) }}</dd>
            <dt class="text-ink-muted">Q1</dt><dd class="text-ink">{{ fmtNum(numericBranch.q1, 4) }}</dd>
            <dt class="text-ink-muted">Q3</dt><dd class="text-ink">{{ fmtNum(numericBranch.q3, 4) }}</dd>
            <dt class="text-ink-muted">IQR</dt><dd class="text-ink">{{ fmtNum(numericBranch.iqr, 4) }}</dd>
            <dt class="text-ink-muted">outliers</dt><dd class="text-ink">{{ fmtNum(numericBranch.outlier_count) }}</dd>
          </dl>
        </section>

        <section v-if="varcharBranch" class="mb-4">
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-wide text-ink-subtle">Strings</h3>
          <dl class="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono tabular-nums">
            <dt class="text-ink-muted">min length</dt>
            <dd class="text-ink">
              {{ fmtNum(varcharBranch.min_length) }}
              <span v-if="varcharBranch.min_length_first_row !== null" class="text-ink-subtle">
                (row {{ fmtNum(varcharBranch.min_length_first_row) }})
              </span>
            </dd>
            <dt class="text-ink-muted">max length</dt>
            <dd class="text-ink">
              {{ fmtNum(varcharBranch.max_length) }}
              <span v-if="varcharBranch.max_length_first_row !== null" class="text-ink-subtle">
                (row {{ fmtNum(varcharBranch.max_length_first_row) }})
              </span>
            </dd>
            <dt class="text-ink-muted">empty</dt><dd class="text-ink">{{ fmtNum(varcharBranch.empty_string_count) }}</dd>
            <dt class="text-ink-muted">whitespace-only</dt><dd class="text-ink">{{ fmtNum(varcharBranch.whitespace_only_count) }}</dd>
            <dt class="text-ink-muted">lead/trail spaces</dt><dd class="text-ink">{{ fmtNum(varcharBranch.leading_or_trailing_space_count) }}</dd>
            <dt class="text-ink-muted">contains spaces</dt><dd class="text-ink">{{ fmtNum(varcharBranch.contains_space_count) }}</dd>
            <dt class="text-ink-muted">email-ish</dt><dd class="text-ink">{{ fmtNum(varcharBranch.email_match_count) }}</dd>
          </dl>
        </section>

        <section v-if="varcharBranch" class="mb-4">
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-wide text-ink-subtle">
            Type-sniff preview
            <span class="ml-1 normal-case text-ink-subtle">% of non-nulls that TRY_CAST cleanly</span>
          </h3>
          <dl class="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono tabular-nums">
            <dt class="text-ink-muted">DATE</dt><dd class="text-ink">{{ fmtPct(varcharBranch.type_sniff.date_pct) }}</dd>
            <dt class="text-ink-muted">TIMESTAMP</dt><dd class="text-ink">{{ fmtPct(varcharBranch.type_sniff.timestamp_pct) }}</dd>
            <dt class="text-ink-muted">INTEGER</dt><dd class="text-ink">{{ fmtPct(varcharBranch.type_sniff.integer_pct) }}</dd>
            <dt class="text-ink-muted">DOUBLE</dt><dd class="text-ink">{{ fmtPct(varcharBranch.type_sniff.double_pct) }}</dd>
            <dt class="text-ink-muted">BOOLEAN</dt><dd class="text-ink">{{ fmtPct(varcharBranch.type_sniff.boolean_pct) }}</dd>
          </dl>
        </section>

        <section v-if="temporalBranch" class="mb-4">
          <h3 class="mb-2 text-2xs font-medium uppercase tracking-wide text-ink-subtle">Temporal</h3>
          <dl class="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono tabular-nums">
            <dt class="text-ink-muted">future dates</dt><dd class="text-ink">{{ fmtNum(temporalBranch.future_count) }}</dd>
            <dt class="text-ink-muted">unique years</dt><dd class="text-ink">{{ fmtNum(temporalBranch.unique_years) }}</dd>
            <dt class="text-ink-muted">range (days)</dt><dd class="text-ink">{{ fmtNum(temporalBranch.range_days) }}</dd>
            <dt class="text-ink-muted">earliest</dt><dd class="truncate text-ink">{{ temporalBranch.earliest ?? '—' }}</dd>
            <dt class="text-ink-muted">latest</dt><dd class="truncate text-ink">{{ temporalBranch.latest ?? '—' }}</dd>
          </dl>
        </section>
      </template>
    </div>
  </aside>
</template>
