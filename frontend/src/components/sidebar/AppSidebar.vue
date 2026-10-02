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
import { navigation } from '../../lib/navigation';

const { state, selectProject, setPage } = useWorkspace();
const query = ref('');
const sidebarSlot = ref<HTMLElement>();
const keyboardNavigation = ref(false);
const onKeyboard = () => (keyboardNavigation.value = true);
const onPointer = () => (keyboardNavigation.value = false);
let pendingFocus: { from: Element; selector: string } | undefined;
let focusSequence = 0;
let mounted = true;
onBeforeUnmount(() => {
  mounted = false;
  focusSequence++;
  pendingFocus = undefined;
  document.removeEventListener('keydown', onKeyboard, true);
  document.removeEventListener('pointerdown', onPointer, true);
});
const collapsed = ref(localStorage.getItem('evograph.sidebar') !== 'open');

watch(
  collapsed,
  (value) => {
    focusSequence++;
    const active = document.activeElement;
    const leaving = sidebarSlot.value?.querySelector(value ? '.sidebar' : '.sidebar-rail');
    pendingFocus =
      active && leaving?.contains(active)
        ? {
            from: active,
            selector: value ? '.sidebar-toggle' : '.project-search input',
          }
        : undefined;
  },
  { flush: 'sync' },
);
watch(
  collapsed,
  async (value) => {
    const focus = pendingFocus;
    const sequence = focusSequence;
    pendingFocus = undefined;
    // Wait for the new pane to lose inert before transferring body-owned focus.
    // The persistent toggle never needs to relinquish focus.
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
  document.addEventListener('keydown', onKeyboard, true);
  document.addEventListener('pointerdown', onPointer, true);
  document.documentElement.dataset.sidebar = collapsed.value ? 'collapsed' : 'open';
});

defineEmits<{ create: []; recover: [] }>();
</script>
<template>
  <div
    ref="sidebarSlot"
    class="sidebar-slot"
    :class="{ 'is-collapsed': collapsed, 'is-keyboard': keyboardNavigation }"
  >
    <header class="sidebar-header">
      <div class="sidebar-brand" aria-label="EvoGraph 项目演化工作台">
        <span class="sidebar-brand-mark"><GitBranch :size="23" aria-hidden="true" /></span>
        <strong class="sidebar-brand-label" :aria-hidden="collapsed"
          >EvoGraph<span>项目演化工作台</span></strong
        >
      </div>
      <button
        class="sidebar-toggle"
        type="button"
        :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'"
        :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
        :aria-expanded="!collapsed"
        aria-controls="sidebar-project-navigation"
        @click="collapsed = !collapsed"
      >
        <PanelLeftOpen v-if="collapsed" :size="20" aria-hidden="true" />
        <PanelLeftClose v-else :size="20" aria-hidden="true" />
        <span class="sidebar-toggle-label" aria-hidden="true">收起侧边栏</span>
      </button>
    </header>
    <nav class="sidebar-rail" :inert="!collapsed" aria-label="工作空间导航">
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
      <div class="rail-bottom">
        <button type="button" aria-label="已删除项目" title="已删除项目" @click="$emit('recover')">
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
    <aside id="sidebar-project-navigation" class="sidebar" :inert="collapsed">
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
  </div>
</template>

<style scoped>
/* One layout owner: animating its actual width keeps the workspace edge and
   the sidebar edge together. No translated main surface or opposing logos. */
.sidebar-slot {
  --sidebar-open-width: 220px;
  --sidebar-rail-width: 64px;
  --sidebar-inset: 10px;
  position: relative;
  flex: 0 0 auto;
  width: var(--sidebar-open-width);
  height: 100%;
  z-index: 35;
  overflow: hidden;
  border-right: 1px solid #d7dfeb88;
  background: #ffffff52;
  transition: width 260ms cubic-bezier(0.23, 1, 0.32, 1);
}
.sidebar-slot.is-collapsed {
  width: var(--sidebar-rail-width);
}
.sidebar-header {
  position: relative;
  height: 122px;
}
.sidebar-brand {
  position: absolute;
  top: 18px;
  left: var(--sidebar-inset);
  height: 44px;
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.sidebar-brand-mark {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  color: var(--accent);
}
.sidebar-brand-label {
  font-size: 19px;
  font-weight: 650;
  letter-spacing: -0.5px;
}
.sidebar-brand-label > span {
  display: block;
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 0;
}
.sidebar-toggle {
  position: absolute;
  top: 70px;
  left: var(--sidebar-inset);
  width: calc(100% - 2 * var(--sidebar-inset));
  min-width: 44px;
  height: 44px;
  margin: 0;
  padding: 0 11px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: flex-start;
}
.sidebar-toggle > svg {
  flex-shrink: 0;
}
.sidebar-toggle:hover {
  color: var(--accent);
  background: #fff;
}
.sidebar-toggle:focus-visible {
  outline-offset: -3px;
}
.sidebar-toggle-label {
  position: absolute;
  left: 52px;
  font-size: 12px;
  white-space: nowrap;
  pointer-events: none;
}
.sidebar-brand-label,
.sidebar-toggle-label {
  transition: opacity 120ms ease;
}
.is-collapsed .sidebar-brand-label,
.is-collapsed .sidebar-toggle-label {
  opacity: 0;
}
.sidebar-slot > .sidebar,
.sidebar-slot > .sidebar-rail {
  position: absolute;
  inset: 122px auto 0 0;
  height: auto;
  margin: 0;
  border: 0;
  background: transparent;
  transition:
    opacity 100ms ease,
    visibility 0s linear 100ms;
}
.sidebar-slot > .sidebar {
  display: flex;
  width: var(--sidebar-open-width);
  padding: 0 12px 12px;
  opacity: 1;
  visibility: visible;
  transition-delay: 70ms, 0s;
}
.sidebar-slot > .sidebar-rail {
  width: var(--sidebar-rail-width);
  padding: 0 0 17px;
  opacity: 0;
  visibility: hidden;
}
.sidebar-slot.is-collapsed > .sidebar {
  opacity: 0;
  visibility: hidden;
  transition-delay: 0s, 100ms;
}
.sidebar-slot.is-collapsed > .sidebar-rail {
  opacity: 1;
  visibility: visible;
  transition-delay: 100ms, 0s;
}
.sidebar-slot.is-keyboard,
.sidebar-slot.is-keyboard * {
  transition: none !important;
  transition-delay: 0s !important;
}
@media (max-width: 1200px) {
  .sidebar-slot {
    --sidebar-open-width: 190px;
  }
}
@media (max-width: 760px) {
  .sidebar-slot {
    --sidebar-rail-width: 52px;
    --sidebar-inset: 4px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .sidebar-slot,
  .sidebar-slot * {
    transition: none !important;
    transition-delay: 0s !important;
  }
}
</style>
