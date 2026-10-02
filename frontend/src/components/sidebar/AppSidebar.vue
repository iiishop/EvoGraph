<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  Plus,
  GitBranch,
  HardDrive,
  PanelLeftClose,
  PanelLeftOpen,
  FolderOpen,
  ArchiveRestore,
  Settings2,
  X,
} from 'lucide-vue-next';
import ProjectItem from './ProjectItem.vue';
import SidebarNavItem from './SidebarNavItem.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import { useSurfaceMotion } from '../../composables/useSurfaceMotion';
import { navigation } from '../../lib/navigation';

const { state, selectProject, setPage } = useWorkspace();
const query = ref('');
const sidebarMotion = useSurfaceMotion('left');
const railMotion = useSurfaceMotion('left');
const layoutMotion = useSurfaceMotion('left');
const sidebarSlot = ref<HTMLElement>();
let previousMainLeft = 0;
let pendingFocus: { from: Element; selector: string } | undefined;
let focusSequence = 0;
let mounted = true;
onBeforeUnmount(() => {
  mounted = false;
  focusSequence++;
  pendingFocus = undefined;
});
const mainSurface = () => sidebarSlot.value?.parentElement?.querySelector<HTMLElement>('.app-main');
const collapsed = ref(localStorage.getItem('evograph.sidebar') !== 'open');

watch(
  collapsed,
  (value) => {
    focusSequence++;
    previousMainLeft = mainSurface()?.getBoundingClientRect().left ?? 0;
    const active = document.activeElement;
    const leaving = sidebarSlot.value?.querySelector(value ? '.sidebar' : '.sidebar-rail');
    pendingFocus =
      active && leaving?.contains(active)
        ? {
            from: active,
            selector: value
              ? '.rail-brand'
              : active.matches('.rail-brand')
                ? '.sidebar-toggle'
                : '.project-search input',
          }
        : undefined;
  },
  { flush: 'sync' },
);
watch(
  collapsed,
  async (value) => {
    const main = mainSurface();
    if (main) layoutMotion.move(main, previousMainLeft);
    const focus = pendingFocus;
    const sequence = focusSequence;
    pendingFocus = undefined;
    // v-show/Transition enter hooks restore display and inert after this
    // post-flush watcher. Wait for that boundary before focusing the new pane.
    await nextTick();
    if (!mounted || sequence !== focusSequence || collapsed.value !== value) return;
    // Transfer only focus owned by the departing navigation surface. If a
    // newer action focused a control elsewhere, that newer intent wins.
    if (
      focus &&
      (document.activeElement === focus.from || document.activeElement === document.body)
    )
      sidebarSlot.value?.querySelector<HTMLElement>(focus.selector)?.focus({ preventScroll: true });
  },
  { flush: 'post' },
);

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

defineEmits<{ create: []; recover: [] }>();
</script>
<template>
  <div ref="sidebarSlot" class="sidebar-slot" :class="{ 'is-collapsed': collapsed }">
    <Transition
      :css="false"
      @enter="railMotion.enter"
      @leave="railMotion.leave"
      @enter-cancelled="railMotion.cancel"
      @leave-cancelled="railMotion.cancel"
    >
      <nav v-show="collapsed" class="sidebar-rail" :inert="!collapsed" aria-label="工作空间导航">
        <button
          class="rail-brand"
          type="button"
          aria-label="展开侧边栏"
          title="展开侧边栏"
          @click="collapsed = false"
        >
          <GitBranch :size="23" aria-hidden="true" />
        </button>
        <button
          type="button"
          :class="{ active: state.page === 'projects' }"
          aria-label="选择项目"
          title="选择项目"
          @click="
            setPage('projects');
            collapsed = false;
          "
        >
          <FolderOpen :size="21" aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label="展开项目列表"
          title="展开项目列表"
          @click="collapsed = false"
        >
          <PanelLeftOpen :size="20" aria-hidden="true" />
        </button>
        <div class="rail-bottom">
          <button
            type="button"
            aria-label="已删除项目"
            title="已删除项目"
            @click="$emit('recover')"
          >
            <ArchiveRestore :size="20" aria-hidden="true" />
          </button>
          <button type="button" aria-label="新建项目" title="新建项目" @click="$emit('create')">
            <Plus :size="23" aria-hidden="true" />
          </button>
          <button
            type="button"
            :class="{ active: state.page === 'settings' }"
            aria-label="设置"
            title="设置"
            @click="setPage('settings')"
          >
            <Settings2 :size="22" aria-hidden="true" />
          </button>
        </div>
      </nav>
    </Transition>
    <Transition
      :css="false"
      @enter="sidebarMotion.enter"
      @leave="sidebarMotion.leave"
      @enter-cancelled="sidebarMotion.cancel"
      @leave-cancelled="sidebarMotion.cancel"
    >
      <aside v-show="!collapsed" class="sidebar" :inert="collapsed">
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
          <input
            v-model="query"
            type="search"
            aria-label="筛选项目"
            placeholder="筛选项目…"
          /><button
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
        <button class="recover-projects" type="button" @click="$emit('recover')">
          <ArchiveRestore :size="15" aria-hidden="true" /> 已删除项目
        </button>
        <div class="sidebar-bottom">
          <div class="sidebar-footer">
            <HardDrive :size="17" aria-hidden="true" />
            <div>本地存储<small>项目与证据保存在此设备</small></div>
          </div>
        </div>
      </aside>
    </Transition>
  </div>
</template>

<style scoped>
.sidebar-slot {
  position: relative;
  flex: 0 0 220px;
  width: 220px;
  height: 100%;
  z-index: 35;
}
.sidebar-slot.is-collapsed {
  flex-basis: 64px;
  width: 64px;
}
.sidebar-slot > .sidebar,
.sidebar-slot > .sidebar-rail {
  position: absolute;
  inset: 0 auto 0 0;
  height: 100%;
}
/* v-show owns visibility, including the retained closing frame. */
.sidebar-slot > .sidebar {
  display: flex;
}
@media (max-width: 1200px) {
  .sidebar-slot:not(.is-collapsed) {
    flex-basis: 190px;
    width: 190px;
  }
}
@media (max-width: 760px) {
  .sidebar-slot.is-collapsed {
    flex-basis: 52px;
    width: 52px;
  }
}
</style>
