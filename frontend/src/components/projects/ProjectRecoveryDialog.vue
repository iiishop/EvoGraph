<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { ArchiveRestore, FolderOpen, RefreshCw, Search, X } from 'lucide-vue-next';
import AppModal from '../ui/AppModal.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import type { ArchivedProjectSummary } from '../../types';

const emit = defineEmits<{ close: [] }>();
const { state, loadArchivedProjects, restoreProject, refreshAfterRestore, selectProject } =
  useWorkspace();
const query = ref('');
const searchInput = ref<HTMLInputElement>();
const refreshing = ref(false);
let restoreTrigger: HTMLElement | null = null;
const matching = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase();
  return state.archivedProjects
    .filter((project) =>
      [project.name, project.description, project.repository].some((value) =>
        value.toLocaleLowerCase().includes(needle),
      ),
    )
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
});
const updatedLabel = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '更新时间未知' : `记录更新于 ${date.toLocaleString()}`;
};
function restore(project: ArchivedProjectSummary, event: MouseEvent) {
  restoreTrigger = event.currentTarget as HTMLElement;
  void restoreProject(project);
}
async function retryRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await refreshAfterRestore();
  } finally {
    refreshing.value = false;
  }
}
function openRestored() {
  const id = state.recoveryRestored?.id;
  if (!id) return;
  emit('close');
  void selectProject(id);
}
// A removed restore button must not leave keyboard focus behind. Do not steal
// focus if the user moved to search, another action, or dismissed the dialog.
watch(
  () => state.recoveryRestored,
  async () => {
    await nextTick();
    if (
      restoreTrigger &&
      !restoreTrigger.isConnected &&
      (document.activeElement === document.body || document.activeElement?.tagName === 'DIALOG')
    )
      searchInput.value?.focus();
    restoreTrigger = null;
  },
);
onMounted(() => {
  searchInput.value?.focus();
  void loadArchivedProjects();
});
</script>
<template>
  <AppModal title="已删除项目" aria-label="已删除项目" wide @close="emit('close')">
    <section class="project-recovery" aria-label="项目恢复" aria-describedby="recovery-preserved">
      <div class="recovery-intro">
        <span class="recovery-intro-icon"><ArchiveRestore :size="22" aria-hidden="true" /></span>
        <div>
          <h3>让项目回到工作空间</h3>
          <p id="recovery-preserved">
            删除只会将项目移出列表，仓库文件始终保留。恢复后可继续查看原有里程碑、对话与证据。
          </p>
        </div>
      </div>
      <div class="recovery-toolbar">
        <label class="recovery-search">
          <Search :size="16" aria-hidden="true" />
          <input
            ref="searchInput"
            v-model="query"
            type="search"
            autofocus
            aria-label="搜索已删除项目"
            placeholder="搜索名称、描述或仓库路径"
          />
          <button
            v-if="query"
            type="button"
            class="icon-button small"
            aria-label="清除搜索"
            @click="
              query = '';
              searchInput?.focus();
            "
          >
            <X :size="15" aria-hidden="true" />
          </button>
        </label>
        <button
          type="button"
          class="button subtle recovery-refresh"
          :disabled="state.recoveryLoading"
          @click="loadArchivedProjects"
        >
          <RefreshCw :size="15" aria-hidden="true" />{{
            state.recoveryLoading ? '读取中…' : '刷新列表'
          }}
        </button>
      </div>
      <div v-if="state.recoveryRestored" class="recovery-confirmed" role="status">
        <div>
          <strong>{{
            state.recoveryRestored.restored
              ? `已恢复「${state.recoveryRestored.name}」`
              : `「${state.recoveryRestored.name}」已在工作空间中`
          }}</strong>
          <span>{{
            state.recoveryRestored.restored
              ? '项目已加入工作空间，仓库文件未改动'
              : '无需重复恢复，当前项目与未发送的草稿已保留'
          }}</span>
        </div>
        <button class="text-button" type="button" :disabled="state.busy" @click="openRestored">
          打开项目
        </button>
      </div>
      <div v-if="state.recoveryRestored?.refreshError" class="recovery-warning" role="alert">
        <p>{{ state.recoveryRestored.refreshError }}</p>
        <button
          type="button"
          class="text-button"
          :disabled="state.busy || refreshing"
          @click="retryRefresh"
        >
          {{ refreshing ? '刷新中…' : '重试刷新工作空间' }}
        </button>
      </div>
      <div v-if="state.recoveryListError" class="recovery-warning" role="alert">
        <p>读取已删除项目失败：{{ state.recoveryListError }}</p>
        <button
          type="button"
          class="text-button"
          :disabled="state.recoveryLoading"
          @click="loadArchivedProjects"
        >
          重试读取
        </button>
      </div>
      <p v-if="state.recoveryLoading" class="recovery-count" role="status">正在读取已删除项目…</p>
      <p
        v-else-if="state.recoveryLoaded && !state.recoveryListError"
        class="recovery-count"
        role="status"
      >
        {{ query.trim() ? `${matching.length} 个匹配 · ` : '' }}共
        {{ state.archivedProjects.length }} 个已删除项目
      </p>
      <p v-if="state.busy" class="recovery-busy" role="status">
        {{
          state.restoringId
            ? '正在恢复项目，可以关闭此窗口，操作会继续完成'
            : '工作空间有操作正在进行，完成后即可恢复项目'
        }}
      </p>
      <ul
        v-if="matching.length"
        class="recovery-list"
        aria-label="可恢复项目"
        :aria-busy="state.recoveryLoading"
      >
        <li v-for="project in matching" :key="project.id" class="recovery-project">
          <div class="recovery-project-main">
            <div class="recovery-project-heading">
              <FolderOpen :size="18" aria-hidden="true" />
              <h3>{{ project.name }}</h3>
              <span v-if="project.is_demo" class="recovery-demo">示例</span>
            </div>
            <p v-if="project.description" class="recovery-description">{{ project.description }}</p>
            <p class="recovery-repository">{{ project.repository || '未关联仓库' }}</p>
            <div class="recovery-metadata">
              <span>{{ project.milestone_count }} 个里程碑</span
              ><span>{{ updatedLabel(project.updated_at) }}</span>
            </div>
          </div>
          <button
            type="button"
            class="button recovery-restore"
            :disabled="state.busy"
            :aria-label="`恢复项目 ${project.name}`"
            @click="restore(project, $event)"
          >
            <ArchiveRestore :size="15" aria-hidden="true" />{{
              state.restoringId === project.id ? '恢复中…' : '恢复项目'
            }}
          </button>
          <div
            v-if="state.recoveryError?.id === project.id"
            class="recovery-project-error"
            role="alert"
          >
            <p>恢复未完成：{{ state.recoveryError.message }}</p>
            <p>如果仓库已有活跃项目，请先在项目列表中查看该项目，确认关联后再重试。</p>
          </div>
        </li>
      </ul>
      <div
        v-else-if="!state.recoveryLoading && state.recoveryLoaded && !state.recoveryListError"
        class="recovery-empty"
      >
        <ArchiveRestore :size="30" aria-hidden="true" />
        <h3>{{ query.trim() ? '没有匹配的已删除项目' : '没有待恢复的项目' }}</h3>
        <p>
          {{
            query.trim()
              ? '试试其他名称、描述或仓库路径'
              : '以后删除的项目会出现在这里，随时可以回来恢复'
          }}
        </p>
        <button
          v-if="query.trim()"
          type="button"
          class="text-button"
          @click="
            query = '';
            searchInput?.focus();
          "
        >
          清除搜索
        </button>
      </div>
      <footer class="recovery-footer">
        <span>仅恢复项目记录，不创建副本或改写仓库</span
        ><button type="button" class="button subtle" @click="emit('close')">关闭</button>
      </footer>
    </section>
  </AppModal>
</template>
