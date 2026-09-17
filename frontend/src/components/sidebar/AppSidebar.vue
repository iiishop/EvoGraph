<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Plus, GitBranch, HardDrive, PanelLeftClose, PanelLeftOpen, X } from 'lucide-vue-next';
import ProjectItem from './ProjectItem.vue';
import SidebarNavItem from './SidebarNavItem.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import { navigation } from '../../lib/navigation';

const { state, selectProject, setPage } = useWorkspace();
const query = ref('');
const collapsed = ref(localStorage.getItem('evograph.sidebar') === 'collapsed');

// 「我最近在哪个项目」由本机打开顺序回答，不动后端接口
function readRecent(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem('evograph.recent') ?? '[]');
    return Array.isArray(raw) ? raw.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
  }
}
const recent = ref<string[]>(readRecent());

const matching = computed(() => {
  const needle = query.value.trim().toLowerCase();
  return needle
    ? state.projects.filter((p) => p.name.toLowerCase().includes(needle))
    : [...state.projects];
});
const visible = computed(() => {
  const rank = new Map(recent.value.map((id, index) => [id, index]));
  const fallback = Number.MAX_SAFE_INTEGER;
  return [...matching.value].sort(
    (a, b) => (rank.get(a.id) ?? fallback) - (rank.get(b.id) ?? fallback),
  );
});
const searching = computed(() => Boolean(query.value.trim()));
// 筛选把当前项目藏起来时，它仍要留在视野里，否则侧边栏一点"你在哪"的线索都没有
const pinnedCurrent = computed(() => {
  const current = state.project;
  if (!current || !searching.value) return null;
  if (visible.value.some((p) => p.id === current.id)) return null;
  return state.projects.find((p) => p.id === current.id) ?? null;
});
const noMatch = computed(() => searching.value && visible.value.length === 0);

watch(
  () => state.project?.id,
  (id) => {
    if (!id) return;
    recent.value = [id, ...recent.value.filter((entry) => entry !== id)].slice(0, 20);
    localStorage.setItem('evograph.recent', JSON.stringify(recent.value));
  },
  { immediate: true },
);
watch(collapsed, (value) => {
  localStorage.setItem('evograph.sidebar', value ? 'collapsed' : 'open');
  document.documentElement.dataset.sidebar = value ? 'collapsed' : 'open';
});
onMounted(() => {
  document.documentElement.dataset.sidebar = collapsed.value ? 'collapsed' : 'open';
});

defineEmits<{ create: [] }>();
</script>
<template>
  <button
    v-if="collapsed"
    type="button"
    class="sidebar-reopen"
    aria-label="展开侧边栏"
    title="展开侧边栏"
    @click="collapsed = false"
  >
    <PanelLeftOpen :size="16" aria-hidden="true" />
  </button>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-mark"><GitBranch :size="20" aria-hidden="true" /></span
      ><strong>EvoGraph<span>项目演化工作台</span></strong
      ><button
        type="button"
        class="icon-button small sidebar-toggle"
        aria-label="收起侧边栏"
        title="收起侧边栏"
        @click="collapsed = true"
      >
        <PanelLeftClose :size="16" aria-hidden="true" />
      </button>
    </div>
    <nav aria-label="主导航">
      <SidebarNavItem
        v-for="item in navigation"
        :key="item.id"
        :icon="item.icon"
        :label="item.label"
        :count="item.countProjects ? matching.length : undefined"
        :active="state.page === item.id"
        @select="setPage(item.id)"
      />
    </nav>
    <div class="section-label">
      <span>我的项目</span
      ><span v-if="searching" class="section-filter-count"
        >{{ matching.length }}/{{ state.projects.length }}</span
      >
    </div>
    <div class="project-search">
      <input v-model="query" type="search" aria-label="筛选项目" placeholder="筛选项目…" /><button
        v-if="query"
        type="button"
        class="icon-button small"
        aria-label="清除筛选"
        title="清除筛选"
        @click="query = ''"
      >
        <X :size="14" aria-hidden="true" />
      </button>
    </div>
    <div class="project-list">
      <ProjectItem
        v-if="pinnedCurrent"
        :project="pinnedCurrent"
        :active="true"
        pinned
        @select="selectProject(pinnedCurrent.id)"
      />
      <ProjectItem
        v-for="project in visible"
        :key="project.id"
        :project="project"
        :active="state.project?.id === project.id && state.page === 'projects'"
        @select="selectProject(project.id)"
      />
      <p v-if="noMatch" class="project-empty">
        没有匹配「{{ query.trim() }}」的项目<button
          type="button"
          class="text-button"
          @click="query = ''"
        >
          清除筛选
        </button>
      </p>
    </div>
    <button class="add-project" @click="$emit('create')">
      <Plus :size="15" aria-hidden="true" /> 新建项目
    </button>
    <div class="sidebar-bottom">
      <div class="sidebar-footer">
        <HardDrive :size="17" aria-hidden="true" />
        <div>本地存储<small>项目与证据保存在此设备</small></div>
      </div>
    </div>
  </aside>
</template>
