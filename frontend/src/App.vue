<script setup lang="ts">
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import TopBar from './components/TopBar.vue'
import Sidebar from './components/Sidebar.vue'
import Splitter from './components/Splitter.vue'
import TableGrid from './components/TableGrid.vue'
import SqlEditor from './components/SqlEditor.vue'
import StatusBar from './components/StatusBar.vue'
import Toasts from './components/Toasts.vue'
import ColumnProfilePanel from './components/ColumnProfilePanel.vue'
import DdlPanel from './components/DdlPanel.vue'
import IngestErrorPanel from './components/IngestErrorPanel.vue'
import RowPivotPanel from './components/RowPivotPanel.vue'
import ExportDialog from './components/ExportDialog.vue'
import ParquetSetDialog from './components/ParquetSetDialog.vue'
import ExcelSheetDialog from './components/ExcelSheetDialog.vue'
import ImportDbDialog from './components/ImportDbDialog.vue'
import IngestOptionsDialog from './components/IngestOptionsDialog.vue'
import DialogHost from './components/DialogHost.vue'
import { useViewStore } from './stores/view'
import { useTablesStore } from './stores/tables'
import { setWindowTitle } from './lib/api'

const SIDEBAR_KEY = 'fq.sidebarWidth'
const RIGHT_KEY   = 'fq.rightWidth'

function loadStoredWidth(key: string, fallback: number, min: number): number {
  const raw = Number(localStorage.getItem(key))
  return Number.isFinite(raw) && raw >= min ? raw : fallback
}

const sidebarWidth = ref<number>(loadStoredWidth(SIDEBAR_KEY, 256, 160))
const rightWidth   = ref<number>(loadStoredWidth(RIGHT_KEY, 340, 240))

watch(sidebarWidth, (w) => { try { localStorage.setItem(SIDEBAR_KEY, String(w)) } catch { /* ignore */ } })
watch(rightWidth,   (w) => { try { localStorage.setItem(RIGHT_KEY, String(w)) } catch { /* ignore */ } })

// Multi-line error text needs more room than a profile panel typically
// uses; widen the right pane when an ingest error opens.
const INGEST_ERROR_MIN_WIDTH = 520

const viewStore = useViewStore()
const { mode, profile, pivot, ddl, ingestError, rightPanelOpen } = storeToRefs(viewStore)
const view = viewStore

// Reflect the active file's path in the OS window title; clears when
// nothing is loaded.
const tablesStore = useTablesStore()
const { activeIngest } = storeToRefs(tablesStore)
watch(
  () => activeIngest.value?.source_path ?? null,
  (p) => { void setWindowTitle(p ?? 'File Quacker') },
  { immediate: true },
)

watch(ingestError, (v) => {
  if (v && rightWidth.value < INGEST_ERROR_MIN_WIDTH) {
    rightWidth.value = INGEST_ERROR_MIN_WIDTH
  }
})

</script>

<template>
  <div class="flex h-full w-full flex-col bg-surface-0 text-ink font-sans">
    <TopBar />

    <div class="flex min-h-0 flex-1">
      <Sidebar :style="{ width: sidebarWidth + 'px' }" />
      <Splitter v-model:size="sidebarWidth" :min="200" :max="520" orientation="vertical" />
      <TableGrid v-if="mode === 'grid'" />
      <SqlEditor v-else />

      <template v-if="rightPanelOpen">
        <Splitter
          v-model:size="rightWidth"
          :min="260"
          :max="720"
          orientation="vertical"
          anchor="end"
        />
        <div class="flex h-full shrink-0" :style="{ width: rightWidth + 'px' }">
          <ColumnProfilePanel
            v-if="profile"
            :source="profile.source"
            :column="profile.column"
            class="w-full"
            @close="viewStore.closeRight()"
          />
          <RowPivotPanel
            v-else-if="pivot"
            :target="pivot"
            class="w-full"
            @close="viewStore.closeRight()"
          />
          <DdlPanel
            v-else-if="ddl"
            :table="ddl.table"
            class="w-full"
            @close="viewStore.closeRight()"
          />
          <IngestErrorPanel
            v-else-if="ingestError"
            :path="ingestError.path"
            :file-name="ingestError.fileName"
            :message="ingestError.message"
            class="w-full"
            @close="viewStore.closeRight()"
          />
        </div>
      </template>
    </div>

    <StatusBar />
    <Toasts />
    <ExportDialog
      v-if="view.exportSource"
      :source="view.exportSource"
      @close="view.closeExport()"
    />
    <ParquetSetDialog />
    <ExcelSheetDialog />
    <ImportDbDialog />
    <IngestOptionsDialog
      v-if="view.ingestOptions"
      :path="view.ingestOptions.path"
      :reload-table="view.ingestOptions.reloadTable ?? null"
      @close="view.closeIngestOptions()"
    />
    <DialogHost />
  </div>
</template>
