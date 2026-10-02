<script setup lang="ts">
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount, useId } from 'vue';
import {
  Network,
  Maximize2,
  ArrowLeft,
  X,
  Layers,
  LoaderCircle,
  SlidersHorizontal,
  Info,
  History,
} from 'lucide-vue-next';
import type { Project } from '../../types';
import DiagramView from './DiagramView.vue';
import ArchitectureBrowser from './ArchitectureBrowser.vue';
import { useArchitectureBrowse } from '../../composables/useArchitectureBrowse';
import { architectureVisibleIds } from '../../lib/architectureBrowse';
import DiagramImage from './DiagramImage.vue';
import ArchitectureQuality from './ArchitectureQuality.vue';
import ComponentPassport from './ComponentPassport.vue';
import UmlView from './UmlView.vue';
import { architectureRole } from '../../lib/architectureRoles';
import { useAgent } from '../../composables/useAgent';
import {
  useScopedClassDetail,
  MAX_DETAIL_COMPONENTS,
  MAX_DETAIL_FILES,
  classDetailGuidance,
  matchingScopedDesigns,
} from '../../composables/useScopedClassDetail';
const agent = useAgent();
const props = defineProps<{ project: Project }>();
const {
  view,
  query,
  focusedId,
  role,
  relation,
  key: browseKey,
  viewport,
  rememberViewport,
} = useArchitectureBrowse(() => props.project);
type ToolbarDisclosure = 'filters' | 'history' | 'legend';
const disclosure = ref<ToolbarDisclosure | null>(null);
const historyOpen = computed({
  get: () => disclosure.value === 'history',
  set: (open) => {
    disclosure.value = open ? 'history' : null;
  },
});
const toolbarId = `architecture-toolbar-${useId()}`;
const toolbar = ref<HTMLElement>();
const disclosurePanel = ref<HTMLElement>();
const scopeToggle = ref<HTMLButtonElement>();
const detailTrigger = ref<HTMLButtonElement>();
const scopeOpen = ref(false);
let toolbarDocument: Document | undefined;
let disclosureFocusRequest = 0;
function toggleDisclosure(value: ToolbarDisclosure) {
  if (disclosure.value === value) {
    dismissDisclosure();
    return;
  }
  disclosure.value = value;
  const focusRequest = ++disclosureFocusRequest;
  const key = contextKey.value;
  void nextTick(() => {
    if (
      disposed ||
      detailOpen.value ||
      key !== contextKey.value ||
      disclosure.value !== value ||
      focusRequest !== disclosureFocusRequest
    )
      return;
    const panel = disclosurePanel.value?.querySelector<HTMLElement>(`#${toolbarId}-${value}`);
    (panel?.querySelector<HTMLElement>('select, button') ?? panel)?.focus({ preventScroll: true });
  });
}
function dismissDisclosure(restoreFocus = false) {
  const previous = disclosure.value;
  const focusRequest = ++disclosureFocusRequest;
  disclosure.value = null;
  if (!restoreFocus || !previous) return;
  const key = contextKey.value;
  void nextTick(() => {
    if (
      !disposed &&
      !detailOpen.value &&
      key === contextKey.value &&
      !disclosure.value &&
      focusRequest === disclosureFocusRequest
    )
      toolbar.value
        ?.querySelector<HTMLButtonElement>(`[data-disclosure-trigger="${previous}"]`)
        ?.focus({ preventScroll: true });
  });
}
function disclosureOwns(target: EventTarget | null) {
  if (!target || !disclosure.value) return false;
  return (
    disclosurePanel.value?.contains(target as Node) ||
    toolbar.value
      ?.querySelector(`[data-disclosure-trigger="${disclosure.value}"]`)
      ?.contains(target as Node)
  );
}
function dismissOutside(event: Event) {
  // Any later interaction owns focus, even another field inside an opening disclosure.
  disclosureFocusRequest++;
  if (disclosure.value && !disclosureOwns(event.target)) dismissDisclosure();
}
function disclosureKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
  // Native option popups consume their own Escape; respect handled events and IME.
  // Search suggestions also stop propagation before this toolbar handler.
  if (event.key === 'Escape' && disclosure.value) {
    event.preventDefault();
    event.stopPropagation();
    dismissDisclosure(true);
  }
}
function toggleScopePicker() {
  dismissDisclosure();
  scopeOpen.value = !scopeOpen.value;
}
const graph = ref<InstanceType<typeof DiagramView>>();
const workbench = ref<HTMLElement>();
const stage = ref<HTMLElement>();
const scopePicker = ref<HTMLElement>();
const stageHeight = ref(250);
let sizeObserver: ResizeObserver | undefined;
let sizeFrame = 0;
let disposed = false;
function measureStage() {
  if (disposed) return;
  cancelAnimationFrame(sizeFrame);
  sizeFrame = requestAnimationFrame(() => {
    if (disposed || !workbench.value || !stage.value || !stage.value.clientWidth) return;
    const top =
      stage.value.getBoundingClientRect().top -
      workbench.value.getBoundingClientRect().top +
      workbench.value.scrollTop;
    stageHeight.value = Math.min(650, Math.max(200, workbench.value.clientHeight - top - 2));
  });
}
function observeWorkbench() {
  if (disposed) return;
  sizeObserver?.disconnect();
  if (workbench.value) sizeObserver?.observe(workbench.value);
  if (scopePicker.value) sizeObserver?.observe(scopePicker.value);
  if (toolbar.value) sizeObserver?.observe(toolbar.value);
  measureStage();
}
onMounted(() => {
  sizeObserver = new ResizeObserver(measureStage);
  observeWorkbench();
  toolbarDocument = toolbar.value?.ownerDocument;
  toolbarDocument?.addEventListener('pointerdown', dismissOutside);
  toolbarDocument?.addEventListener('focusin', dismissOutside);
});
onBeforeUnmount(() => {
  disposed = true;
  cancelAnimationFrame(sizeFrame);
  sizeObserver?.disconnect();
  toolbarDocument?.removeEventListener('pointerdown', dismissOutside);
  toolbarDocument?.removeEventListener('focusin', dismissOutside);
});
const architecture = computed(() =>
  view.value === 'current'
    ? props.project.architectures.at(-1)
    : view.value === 'source'
      ? null
      : props.project.architectures.find((item) => `revision:${item.number}` === view.value),
);
const diagram = computed(() =>
  view.value === 'source' ? props.project.source_diagram : architecture.value?.diagram,
);
const architectureRevision = computed(() =>
  view.value === 'source' ? 0 : (architecture.value?.number ?? -1),
);
const contextLabel = computed(() =>
  view.value === 'source'
    ? 'SRC · 源码现状'
    : `DESIGN · ${view.value === 'current' ? '目标架构' : '历史架构'} A${architecture.value?.number ?? '—'}`,
);
// Include the evidence identity: a refreshed baseline or edited mapping cannot reuse an old result.
const contextKey = computed(() =>
  JSON.stringify([
    props.project.id,
    props.project.created_at,
    view.value,
    architectureRevision.value,
    props.project.source_fingerprint,
    props.project.baselines.at(-1)?.id,
    diagram.value,
  ]),
);
const {
  componentIds,
  filePaths,
  selectFiles,
  toggleFile,
  open: detailOpen,
  loading: detailLoading,
  result: detailResult,
  error: detailError,
  select: selectScope,
  toggle: toggleScope,
  close: closeDetail,
  show: showDetail,
  reload: reloadDetail,
  showSaved,
} = useScopedClassDetail(() => ({
  key: contextKey.value,
  projectId: props.project.id,
  baselineId: props.project.baselines.at(-1)?.id,
  architectureRevision: architectureRevision.value,
  componentIds: diagram.value?.nodes.map((node) => node.id) ?? [],
  componentFiles: Object.fromEntries(
    diagram.value?.nodes.map((node) => [node.id, node.source_refs ?? []]) ?? [],
  ),
}));
watch([contextKey, detailOpen, scopeOpen], async () => {
  await nextTick();
  observeWorkbench();
});
const detailMode = ref<'source' | 'design'>('source');
const savedDesignId = ref('');
const savedDesigns = computed(() =>
  matchingScopedDesigns(
    props.project.uml_diagrams ?? [],
    componentIds.value,
    architectureRevision.value,
  ),
);
const savedDesign = computed(
  () =>
    savedDesigns.value.find((item) => item.id === savedDesignId.value) ?? savedDesigns.value.at(-1),
);
watch(
  [contextKey, componentIds],
  () => {
    detailMode.value = 'source';
    savedDesignId.value = '';
  },
  { flush: 'sync' },
);
const selected = computed(() => diagram.value?.nodes.find((n) => n.id === focusedId.value));
const scopedNodes = computed(() =>
  componentIds.value.flatMap((id) => diagram.value?.nodes.find((n) => n.id === id) ?? []),
);
const availableFiles = computed(() =>
  [...new Set(scopedNodes.value.flatMap((node) => node.source_refs ?? []))].sort(),
);
const truncatedRefs = computed(() =>
  scopedNodes.value.some((node) => (node.source_ref_count ?? 0) > (node.source_refs?.length ?? 0)),
);
const roles = computed(() => [
  ...new Set(diagram.value?.nodes.map((n) => n.role ?? 'backend') ?? []),
]);
const sources = computed(() =>
  props.project.research.filter((r) => architecture.value?.research_ids?.includes(r.id)),
);
watch(
  () => props.project.id,
  () => {
    historyOpen.value = false;
    scopeOpen.value = false;
  },
);
watch(
  () => [agent.state.navigationTick, agent.state.follow[props.project.id]],
  () => {
    if (
      agent.state.projectId !== props.project.id ||
      agent.state.view !== 'architecture' ||
      agent.state.follow[props.project.id] === false
    )
      return;
    historyOpen.value = false;
    if (agent.state.diagramKind === 'class') {
      const scoped = [...(props.project.uml_diagrams ?? [])]
        .reverse()
        .find((item) => item.id === agent.state.diagramId && item.kind === 'class');
      // Legacy whole-project diagrams remain stored, but never become a navigable view.
      if (!scoped?.component_ids?.length || scoped.architecture_revision === undefined) return;
      if (scoped.architecture_revision === 0) view.value = 'source';
      else {
        const index = props.project.architectures.findIndex(
          (item) => item.number === scoped.architecture_revision,
        );
        if (index < 0) return;
        view.value =
          index === props.project.architectures.length - 1
            ? 'current'
            : `revision:${scoped.architecture_revision}`;
      }
      if (!selectScope(scoped.component_ids)) return;
      if (savedDesigns.value.some((item) => item.id === scoped.id)) {
        savedDesignId.value = scoped.id;
        detailMode.value = 'design';
        showSaved();
      }
      return;
    }
    view.value = props.project.architectures.length ? 'current' : 'source';
  },
  { immediate: true },
);
function chooseView(value: string) {
  const returnFocus = disclosure.value === 'history';
  agent.freeView(props.project.id);
  view.value = value;
  dismissDisclosure(returnFocus);
}
function chooseComponent(id: string) {
  if (id && !diagram.value?.nodes.some((node) => node.id === id)) return;
  agent.freeView(props.project.id);
  if (focusedId.value === id && id) graph.value?.locate(id);
  focusedId.value = id;
  if (!id) relation.value = 'all';
}
const visibleIds = computed(() =>
  diagram.value
    ? architectureVisibleIds(
        diagram.value,
        query.value,
        role.value,
        focusedId.value,
        relation.value,
      )
    : new Set<string>(),
);
const hasFilters = computed(() =>
  Boolean(
    query.value.trim() || role.value !== 'all' || focusedId.value || relation.value !== 'all',
  ),
);
const filterCount = computed(
  () =>
    Number(role.value !== 'all') +
    Number(Boolean(focusedId.value)) +
    Number(relation.value !== 'all'),
);
const filterSummary = computed(() =>
  [
    role.value !== 'all' ? architectureRole(role.value).label : '',
    relation.value === 'upstream' ? '上游关系' : relation.value === 'downstream' ? '下游关系' : '',
  ]
    .filter(Boolean)
    .join(' · '),
);
async function showAll() {
  agent.freeView(props.project.id);
  query.value = '';
  role.value = 'all';
  focusedId.value = '';
  relation.value = 'all';
  const key = browseKey.value;
  await nextTick();
  if (key === browseKey.value) graph.value?.fit();
}
function browseManually() {
  agent.freeView(props.project.id);
}

function changeScope(id: string) {
  agent.freeView(props.project.id);
  toggleScope(id);
  scopeOpen.value = true;
}
async function returnToOverview() {
  const key = contextKey.value;
  closeDetail();
  await nextTick();
  if (!disposed && key === contextKey.value)
    (scopeOpen.value ? detailTrigger.value : scopeToggle.value)?.focus({ preventScroll: true });
}
function openDetail() {
  detailMode.value = 'source';
  agent.freeView(props.project.id);
  void showDetail();
}
function openDesign() {
  agent.freeView(props.project.id);
  detailMode.value = 'design';
  showSaved();
}
defineExpose({ fit: () => graph.value?.fit(), reset: () => graph.value?.reset() });
</script>
<template>
  <section ref="workbench" class="architecture-workbench">
    <div
      ref="toolbar"
      v-show="!detailOpen"
      class="architecture-toolbar"
      @keydown="disclosureKeydown"
    >
      <header class="architecture-heading">
        <h2><Network :size="16" />架构</h2>
        <nav class="architecture-tabs" aria-label="架构视图">
          <button
            v-for="item in [
              { id: 'current', label: '目标架构' },
              { id: 'source', label: 'SRC 源码现状' },
            ]"
            :key="item.id"
            :aria-pressed="view === item.id"
            @click="chooseView(item.id)"
          >
            {{ item.label }}
          </button>
        </nav>
        <span v-if="view !== 'source' && view !== 'current'" class="history-identity">
          历史 A{{ architecture?.number }}
          <button class="text-button" @click="chooseView('current')">回到目标架构</button>
        </span>
        <span v-else-if="architecture" class="architecture-revision"
          >A{{ architecture.number }}</span
        >
        <span
          v-if="diagram"
          class="architecture-browse-status"
          role="status"
          :title="`${visibleIds.size}/${diagram.nodes.length} 个组件高亮 · 其余组件及关系仍保留`"
        >
          {{
            hasFilters
              ? `${visibleIds.size}/${diagram.nodes.length} 高亮`
              : `${diagram.nodes.length} 组件`
          }}
          <span>· {{ diagram.edges.length }} 关系</span>
        </span>
        <div class="architecture-heading-actions">
          <button
            class="toolbar-button"
            data-disclosure-trigger="history"
            :aria-expanded="historyOpen"
            :aria-controls="`${toolbarId}-history`"
            @click="toggleDisclosure('history')"
          >
            <History :size="14" /><span>版本记录 {{ project.architectures.length }}</span>
          </button>
          <button
            v-if="diagram"
            class="toolbar-button architecture-scope-toggle"
            ref="scopeToggle"
            :aria-expanded="scopeOpen"
            :aria-controls="`${toolbarId}-scope`"
            @click="toggleScopePicker"
          >
            <Layers :size="14" /><span
              >模块范围{{
                componentIds.length ? ` ${componentIds.length}/${MAX_DETAIL_COMPONENTS}` : ''
              }}</span
            >
          </button>
          <button
            v-if="diagram"
            class="icon-button toolbar-info"
            aria-label="图例与浏览说明"
            data-disclosure-trigger="legend"
            :aria-expanded="disclosure === 'legend'"
            :aria-controls="`${toolbarId}-legend`"
            @click="toggleDisclosure('legend')"
          >
            <Info :size="16" />
          </button>
        </div>
      </header>
      <div v-if="diagram" class="architecture-tools">
        <ArchitectureBrowser
          :key="browseKey"
          :nodes="diagram.nodes"
          :query="query"
          :role="role"
          :focused-id="focusedId"
          @update:query="
            query = $event;
            browseManually();
          "
          @select="chooseComponent"
        />
        <span v-if="selected" class="architecture-selected" :title="`已定位：${selected.label}`">
          <span>已定位 {{ selected.label }}</span>
          <button
            class="icon-button"
            :aria-label="`取消定位 ${selected.label}`"
            @click="chooseComponent('')"
          >
            <X :size="12" />
          </button>
        </span>
        <button
          class="toolbar-button architecture-filter-toggle"
          data-disclosure-trigger="filters"
          :aria-expanded="disclosure === 'filters'"
          :aria-controls="`${toolbarId}-filters`"
          @click="toggleDisclosure('filters')"
        >
          <SlidersHorizontal :size="14" />筛选<span v-if="filterCount" class="filter-count">{{
            filterCount
          }}</span>
          <span v-if="filterSummary" class="filter-summary">{{ filterSummary }}</span>
        </button>
        <button class="text-button architecture-show-all" @click="showAll">
          {{ hasFilters ? '清除筛选 · 显示全部' : '显示全部组件' }}
        </button>
        <button class="icon-button" aria-label="适应架构画布" @click="graph?.fit()">
          <Maximize2 :size="17" />
        </button>
      </div>
      <div
        ref="disclosurePanel"
        v-show="disclosure"
        class="toolbar-disclosure"
        :inert="disclosure ? undefined : true"
      >
        <div class="toolbar-disclosure-heading">
          <strong>{{
            disclosure === 'filters'
              ? '筛选架构组件'
              : disclosure === 'history'
                ? '架构版本记录'
                : '图例与浏览说明'
          }}</strong>
          <button
            class="icon-button"
            aria-label="关闭架构工具面板"
            @click="dismissDisclosure(true)"
          >
            <X :size="15" />
          </button>
        </div>
        <section
          :id="`${toolbarId}-history`"
          v-show="historyOpen"
          :inert="historyOpen ? undefined : true"
          class="version-track"
          aria-label="架构版本记录"
        >
          <button
            v-for="(a, i) in project.architectures"
            :key="a.number"
            :aria-pressed="
              view === `revision:${a.number}` ||
              (view === 'current' && i === project.architectures.length - 1)
            "
            @click="chooseView(`revision:${a.number}`)"
          >
            <strong>A{{ a.number }}</strong
            ><span>{{ a.summary }}</span
            ><small>{{ i === project.architectures.length - 1 ? '当前版本' : '历史快照' }}</small>
          </button>
          <p v-if="!project.architectures.length">尚无架构版本</p>
        </section>
        <section
          v-if="diagram"
          :id="`${toolbarId}-filters`"
          v-show="disclosure === 'filters'"
          :inert="disclosure === 'filters' ? undefined : true"
          class="architecture-filter-fields"
          aria-label="架构筛选"
        >
          <label
            >组件类型<select v-model="role" aria-label="组件类型" @change="browseManually">
              <option value="all">全部类型</option>
              <option v-for="item in roles" :key="item" :value="item">
                {{ architectureRole(item).label }}
              </option>
            </select></label
          >
          <label
            >架构组件<select
              :value="focusedId"
              aria-label="选择架构组件"
              @change="chooseComponent(($event.target as HTMLSelectElement).value)"
            >
              <option value="">全部组件</option>
              <option v-for="node in diagram.nodes" :key="node.id" :value="node.id">
                {{ node.label }}
              </option>
            </select></label
          >
          <label
            >关系范围<select
              v-model="relation"
              :disabled="!focusedId"
              aria-label="关系范围"
              @change="browseManually"
            >
              <option value="all">全部关系</option>
              <option value="upstream">上游关系</option>
              <option value="downstream">下游关系</option>
            </select></label
          >
          <p>筛选只改变高亮，全部组件和关系仍保留在画布中</p>
        </section>
        <section
          v-if="diagram"
          :id="`${toolbarId}-legend`"
          v-show="disclosure === 'legend'"
          :inert="disclosure === 'legend' ? undefined : true"
          class="architecture-legend-panel"
          aria-label="图例与浏览说明"
          tabindex="-1"
        >
          <div class="architecture-legend">
            <span v-for="item in roles" :key="item"
              ><i :style="{ background: architectureRole(item).color }" />{{
                architectureRole(item).label
              }}</span
            >
          </div>
          <p>
            {{ contextLabel }} · {{ diagram.nodes.length }} 个组件 /
            {{ diagram.edges.length }} 条关系
          </p>
          <p>
            搜索名称、职责或源码路径。↑↓ 浏览，Enter 定位，Esc
            收起搜索建议。筛选仅改变高亮，其余组件及关系仍保留。
          </p>
        </section>
      </div>
    </div>
    <template v-if="diagram">
      <section
        ref="scopePicker"
        :id="`${toolbarId}-scope`"
        v-show="!detailOpen && scopeOpen"
        :inert="!detailOpen && scopeOpen ? undefined : true"
        class="class-scope"
        aria-label="局部类结构范围"
      >
        <div class="class-scope-row">
          <details class="class-scope-picker">
            <summary>
              <Layers :size="15" />选择模块
              <span>{{ componentIds.length }}/{{ MAX_DETAIL_COMPONENTS }}</span>
            </summary>
            <fieldset class="class-scope-options">
              <legend>选择 1–3 个模块</legend>
              <label v-for="node in diagram.nodes" :key="node.id">
                <input
                  type="checkbox"
                  :checked="componentIds.includes(node.id)"
                  :disabled="
                    componentIds.length >= MAX_DETAIL_COMPONENTS && !componentIds.includes(node.id)
                  "
                  @change="changeScope(node.id)"
                />
                <span
                  >{{ node.label
                  }}<small>{{
                    node.source_refs?.length
                      ? `${node.source_refs.length} 条源码依据`
                      : 'DESIGN · 未关联源码'
                  }}</small></span
                >
              </label>
            </fieldset>
          </details>
          <div class="class-scope-chips" aria-live="polite">
            <button
              v-for="node in scopedNodes"
              :key="node.id"
              class="class-scope-chip"
              :aria-label="`移除模块 ${node.label}`"
              @click="changeScope(node.id)"
            >
              {{ node.label }}<X :size="12" />
            </button>
            <span v-if="!componentIds.length" class="class-scope-hint"
              >先选择 1–3 个模块，再下钻查看类结构</span
            >
          </div>
          <button
            ref="detailTrigger"
            class="button secondary class-detail-trigger"
            :disabled="!componentIds.length || filePaths?.length === 0 || detailLoading"
            @click="openDetail"
          >
            查看局部类结构
          </button>
        </div>
        <details v-if="componentIds.length && availableFiles.length" class="class-file-picker">
          <summary>
            缩小到文件（可选）<span>{{
              filePaths === null
                ? `全部映射 · ${availableFiles.length} 个已列出文件`
                : `已选 ${filePaths.length}/${MAX_DETAIL_FILES} 个文件`
            }}</span>
          </summary>
          <label class="class-file-mode"
            ><input
              type="checkbox"
              :checked="filePaths !== null"
              @change="selectFiles(filePaths === null ? [] : null)"
            />仅查看选定文件</label
          >
          <p v-if="truncatedRefs" class="class-scope-note">
            模块文件较多，源码依据列表不完整。请从已列出的文件中缩小范围；不会把局部结果当作整个模块。
          </p>
          <p v-if="filePaths?.length === 0" class="class-scope-note" role="status">
            请选择 1–12 个文件后查看局部类结构
          </p>
          <fieldset v-if="filePaths !== null" class="class-scope-options class-file-options">
            <legend>仅限所选模块的已关联源码</legend>
            <label v-for="file in availableFiles" :key="file"
              ><input
                type="checkbox"
                :checked="filePaths.includes(file)"
                :disabled="filePaths.length >= MAX_DETAIL_FILES && !filePaths.includes(file)"
                @change="toggleFile(file)"
              /><span>{{ file }}</span></label
            >
          </fieldset>
        </details>
        <p class="class-scope-note">
          {{ contextLabel }} · 按模块源码依据提取，支持 Python / TypeScript / Vue / C++ 静态提取
        </p>
      </section>
      <!-- Keep the overview mounted so Back preserves its viewport, focus, filters and version. -->
      <div
        ref="stage"
        v-show="!detailOpen"
        class="architecture-stage"
        :style="{ height: `${stageHeight}px` }"
      >
        <DiagramView
          :key="browseKey"
          ref="graph"
          :diagram="diagram"
          :source="view === 'source'"
          :query="query"
          :role="role"
          :browse-key="browseKey"
          :initial-viewport="viewport"
          @viewport="rememberViewport"
          :focused-id="focusedId"
          :relation="relation"
          @select="chooseComponent"
        />
        <ComponentPassport
          v-if="selected"
          :node="selected"
          :diagram="diagram"
          :project="project"
          :source="view === 'source'"
          @close="chooseComponent('')"
          @select="chooseComponent"
        >
          <template #scope-action>
            <button
              class="button secondary passport-scope-action"
              :disabled="
                componentIds.length >= MAX_DETAIL_COMPONENTS && !componentIds.includes(selected.id)
              "
              :aria-pressed="componentIds.includes(selected.id)"
              @click="changeScope(selected.id)"
            >
              {{ componentIds.includes(selected.id) ? '从局部范围移除' : '加入局部类结构' }}
            </button>
            <small
              v-if="
                componentIds.length >= MAX_DETAIL_COMPONENTS && !componentIds.includes(selected.id)
              "
              class="muted"
              >最多选择 3 个模块，请先移除一个</small
            >
          </template>
        </ComponentPassport>
      </div>
      <section
        v-if="detailOpen"
        class="architecture-class-detail"
        aria-label="局部类结构"
        :aria-busy="detailLoading"
      >
        <header class="class-detail-heading">
          <button class="button secondary" @click="returnToOverview">
            <ArrowLeft :size="15" />返回模块总览
          </button>
          <div>
            <h3>局部类结构</h3>
            <p>
              {{ contextLabel }} · {{ scopedNodes.map((node) => node.label).join(' / ')
              }}{{
                detailMode === 'source' && filePaths ? ` · ${filePaths.length} 个指定文件` : ''
              }}
            </p>
          </div>
          <button class="icon-button" aria-label="关闭局部类结构" @click="returnToOverview">
            <X :size="18" />
          </button>
        </header>
        <nav v-if="savedDesigns.length" class="class-detail-tabs" aria-label="局部类结构依据">
          <button :aria-pressed="detailMode === 'source'" @click="openDetail">
            SRC 源码类结构
          </button>
          <button :aria-pressed="detailMode === 'design'" @click="openDesign">
            DESIGN 设计类结构 · {{ savedDesigns.length }}
          </button>
        </nav>
        <template v-if="detailMode === 'design' && savedDesign">
          <p class="class-detail-disclaimer">
            已保存的局部设计，仅属于当前所选模块和架构版本。DESIGN
            类与成员不代表已实现；混合图中的源码内容保留保存时状态，请切换 SRC 查看当前源码。
          </p>
          <label v-if="savedDesigns.length > 1" class="class-design-selector"
            >选择局部设计<select v-model="savedDesignId">
              <option v-for="item in savedDesigns" :key="item.id" :value="item.id">
                {{ item.title }} · v{{ item.revision }}
              </option>
            </select></label
          >
          <UmlView :diagram="savedDesign" :project-id="project.id" />
          <details v-if="savedDesign.source_refs.length" class="class-detail-files">
            <summary>保存时的源码引用 · {{ savedDesign.source_refs.length }} 个文件</summary>
            <code v-for="file in savedDesign.source_refs" :key="file" class="scope-path">{{
              file
            }}</code>
          </details>
        </template>
        <template v-else>
          <details class="class-detail-disclaimer">
            <summary>SRC · 当前源码提取，设计范围不等于实现证据</summary>
            <p>
              此处展示所选模块已关联源码的静态类结构。目标与历史架构只限定模块范围，不代表设计已实现，也不是历史代码快照。
            </p>
          </details>
          <div v-if="detailLoading" class="class-detail-state" role="status">
            <LoaderCircle :size="24" class="class-detail-spinner" />
            <h3>正在提取所选模块…</h3>
            <p>仅分析当前范围，可随时返回模块总览</p>
          </div>
          <div v-else-if="detailError" class="class-detail-state" role="alert">
            <h3>局部结构暂时无法加载</h3>
            <p>{{ detailError }}</p>
            <button class="button secondary" @click="openDetail">重试当前范围</button>
          </div>
          <template v-else-if="detailResult">
            <p
              v-if="detailResult.status === 'ready'"
              class="class-detail-message visually-hidden"
              role="status"
            >
              {{ detailResult.message }}
            </p>
            <div v-else class="class-detail-state" role="status">
              <h3>{{ detailResult.message }}</h3>
              <p>{{ classDetailGuidance(detailResult.status) }}</p>
              <button class="button secondary" @click="returnToOverview">返回调整模块范围</button>
            </div>
            <UmlView
              v-if="detailResult.status === 'ready' && detailResult.diagram"
              :diagram="detailResult.diagram"
              :project-id="project.id"
              :preview-image="detailResult.image"
              :semantic="detailResult.semantic"
              compact
              :render-status="detailResult.render_status"
              :render-error="detailResult.render_error"
              can-retry-render
              @retry-source-render="reloadDetail"
            />
            <DiagramImage
              v-else-if="detailResult.status === 'ready' && detailResult.image"
              :src="detailResult.image"
              title="所选模块的局部类结构"
            />
            <div
              v-if="detailResult.boundaries.length || detailResult.limitations.length"
              class="class-detail-evidence"
            >
              <div v-if="detailResult.boundaries.length">
                <h4>范围与外部边界</h4>
                <ul>
                  <li v-for="boundary in detailResult.boundaries" :key="boundary">
                    {{ boundary }}
                  </li>
                </ul>
              </div>
              <div v-if="detailResult.limitations.length">
                <h4>提取局限</h4>
                <ul>
                  <li v-for="limitation in detailResult.limitations" :key="limitation">
                    {{ limitation }}
                  </li>
                </ul>
              </div>
            </div>
            <details v-if="detailResult.files.length" class="class-detail-files">
              <summary>源码依据 · {{ detailResult.files.length }} 个文件</summary>
              <code v-for="file in detailResult.files" :key="file" class="scope-path">{{
                file
              }}</code>
            </details>
          </template>
        </template>
      </section>
      <p v-if="view === 'source' && !detailOpen" class="source-summary">
        {{ project.source_summary }}
      </p>
      <ArchitectureQuality v-if="architecture && !detailOpen" :architecture="architecture" />
      <details v-if="architecture && !detailOpen" class="architecture-foundation">
        <summary>
          技术选型、决策与来源
          <span
            >{{ architecture.technologies.length }} 项选型 / {{ sources.length }} 条网页依据</span
          >
        </summary>
        <p>{{ architecture.summary }}</p>
        <div class="technology-grid">
          <article v-for="tech in architecture.technologies" :key="tech.area">
            <small>{{ tech.area }}</small>
            <h3>{{ tech.choice }}</h3>
            <p>{{ tech.rationale }}</p>
          </article>
        </div>
        <ul class="decision-list">
          <li v-for="decision in architecture.decisions" :key="decision">{{ decision }}</li>
        </ul>
        <a
          v-for="source in sources"
          :key="source.id"
          :href="source.url"
          target="_blank"
          rel="noopener noreferrer"
          class="research-link"
          >{{ source.title }}<small>{{ new Date(source.created_at).toLocaleString() }}</small></a
        >
        <p v-if="!sources.length" class="muted">
          尚无网页依据；可在下方要求 Agent 搜索并补充选型依据。
        </p>
      </details>
    </template>
    <div v-else class="empty-state">
      <Network :size="36" />
      <h3>{{ view === 'source' ? '读取基线，自动建立源码视图' : '架构可以稍后补充' }}</h3>
      <p>
        {{
          view === 'source'
            ? '在顶部读取基线后，将显示带 SRC 标识的目录组件和静态导入关系。'
            : '可以先完善里程碑路线图；需要架构时，在下方说明系统边界与技术约束。'
        }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.architecture-workbench {
  container: architecture / inline-size;
}
.architecture-stage {
  flex: 0 0 auto;
  min-height: 200px;
}
.architecture-stage :deep(.design-diagram) {
  min-height: 200px;
  height: 100%;
}
@media (max-width: 700px) {
  .architecture-stage {
    flex-direction: row;
  }
  .architecture-stage :deep(.component-passport) {
    position: absolute;
    z-index: 5;
    right: 0;
    width: min(270px, 70%);
    height: 100%;
    max-height: none;
    border-left: 1px solid var(--line);
  }
}

.architecture-toolbar {
  position: relative;
  z-index: 10;
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}
.architecture-heading {
  min-height: 44px;
  box-sizing: border-box;
  padding: 8px 16px 4px;
  gap: 10px;
  flex-direction: row;
  flex-wrap: nowrap;
  justify-content: flex-start;
  align-items: center;
}
.architecture-heading h2 {
  flex: 0 0 auto;
  gap: 5px;
  margin: 0;
  font-size: 13px;
}
.architecture-tabs {
  flex: 0 0 auto;
  gap: 2px;
  margin: 0;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--canvas);
}
.architecture-tabs button {
  padding: 4px 9px;
  min-height: 25px;
  border: 0;
  border-radius: 5px;
  font-size: 11px;
  white-space: nowrap;
}
.architecture-tabs button[aria-pressed='true'] {
  background: var(--panel, white);
  box-shadow: 0 1px 3px #273a5410;
}
.architecture-revision,
.architecture-browse-status,
.history-identity {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.history-identity {
  display: flex;
  gap: 6px;
  align-items: baseline;
  color: #79571b;
}
.history-identity .text-button {
  font-size: 10px;
}
.architecture-browse-status {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.architecture-heading-actions {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
}
.architecture-browse-status + .architecture-heading-actions {
  margin-left: 0;
}
.toolbar-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  gap: 5px;
  min-height: 30px;
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel, white);
  color: var(--text-secondary);
  font-size: 11px;
  white-space: nowrap;
}
.toolbar-button[aria-expanded='true'],
.toolbar-info[aria-expanded='true'] {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--canvas);
}
.architecture-tools {
  min-height: 48px;
  box-sizing: border-box;
  padding: 4px 16px 10px;
  gap: 8px;
  flex-wrap: nowrap;
}
.architecture-tools :deep(.architecture-browser) {
  flex: 1 1 260px;
  min-width: 160px;
}
.architecture-tools :deep(.component-search input) {
  min-height: 30px;
  padding-top: 5px;
  padding-bottom: 5px;
  font-size: 12px;
}
.architecture-tools > .icon-button,
.toolbar-info {
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
}
.architecture-selected {
  display: flex;
  align-items: center;
  flex: 0 1 auto;
  max-width: 180px;
  min-width: 60px;
  padding-left: 8px;
  border: 1px solid var(--line);
  border-radius: 5px;
  color: var(--accent);
  background: var(--canvas);
  font-size: 11px;
}
.architecture-selected > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.architecture-selected .icon-button {
  flex-shrink: 0;
  width: 24px;
  height: 28px;
}
.filter-count {
  min-width: 15px;
  border-radius: 4px;
  background: var(--canvas);
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.filter-summary {
  color: var(--accent);
}
.architecture-show-all {
  flex-shrink: 0;
  font-size: 11px;
  white-space: nowrap;
}
.toolbar-disclosure {
  position: absolute;
  z-index: 25;
  top: calc(100% - 3px);
  right: 16px;
  width: min(470px, calc(100% - 32px));
  max-height: min(55vh, 360px);
  overflow: auto;
  overscroll-behavior: contain;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--panel, white);
  box-shadow: 0 10px 30px #15243b20;
}
.toolbar-disclosure-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
}
.toolbar-disclosure-heading .icon-button {
  width: 24px;
  height: 24px;
}
.architecture-filter-fields {
  display: grid;
  grid-template-columns: 1fr 1.3fr 1fr;
  gap: 10px;
}
.architecture-filter-fields label {
  display: grid;
  gap: 5px;
  min-width: 0;
  font-size: 11px;
  color: var(--text-secondary);
}
.architecture-filter-fields select {
  width: 100%;
  min-width: 0;
  min-height: 32px;
  padding: 5px;
  font-size: 12px;
}
.architecture-filter-fields p,
.architecture-legend-panel p {
  grid-column: 1 / -1;
  margin: 3px 0 0;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.6;
}
.architecture-legend {
  padding: 0 0 8px;
  gap: 9px 14px;
  font-size: 11px;
}
.version-track {
  display: grid;
  gap: 6px;
  padding: 0;
}
.version-track > button {
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  min-width: 0;
  max-width: none;
  width: 100%;
  padding: 9px;
  gap: 10px;
}
.version-track strong,
.version-track small {
  font-size: 11px;
}
.version-track span {
  font-size: 12px;
}
@container architecture (max-width: 780px) {
  .architecture-heading h2 {
    display: none;
  }
  [data-disclosure-trigger='history'] > span {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .architecture-heading,
  .architecture-tools {
    gap: 6px;
    padding-left: 12px;
    padding-right: 12px;
  }
  .architecture-browse-status > span {
    display: none;
  }
  .architecture-selected {
    max-width: 130px;
  }
  .architecture-tools :deep(.architecture-browser) {
    min-width: 130px;
  }
}
@container architecture (max-width: 600px) {
  .architecture-heading {
    flex-wrap: wrap;
    row-gap: 6px;
  }
  .architecture-heading-actions {
    margin-left: auto;
  }
  .architecture-heading h2 {
    display: none;
  }
  .architecture-tools {
    flex-wrap: wrap;
    padding-top: 6px;
  }
  .architecture-tools :deep(.architecture-browser) {
    flex-basis: calc(100% - 50px);
  }
  .architecture-filter-fields {
    grid-template-columns: 1fr;
  }
}
.class-detail-trigger {
  min-height: 30px;
  padding: 5px 9px;
  font-size: 11px;
}

.class-scope {
  margin: 8px 16px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--canvas);
}
.class-scope-row,
.class-scope-chips,
.class-detail-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}
.class-scope-row {
  flex-wrap: wrap;
}
.class-scope-picker {
  flex: 0 0 auto;
}
.class-scope-picker summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  cursor: pointer;
}
.class-scope-picker summary span {
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.class-scope-picker[open] {
  flex-basis: 100%;
}
.class-scope-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 6px 12px;
  max-height: 180px;
  overflow: auto;
  margin: 10px 0 0;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
}
.class-scope-options legend {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 0 4px;
}
.class-scope-options label {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 5px;
  font-size: 12px;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.class-scope-options input {
  width: 15px;
  height: 15px;
  margin-top: 2px;
  flex-shrink: 0;
  accent-color: var(--accent);
}
.class-scope-options label:has(input:disabled) {
  opacity: 0.5;
  cursor: not-allowed;
}
.class-scope-options small {
  display: block;
  color: var(--text-secondary);
  font-size: 10px;
  margin-top: 2px;
}
.class-scope-chips {
  flex: 1;
  flex-wrap: wrap;
  min-width: 0;
}
.class-scope-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 20px;
  background: white;
  color: var(--ink);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.class-scope-chip svg {
  flex-shrink: 0;
}
.class-scope-hint {
  font-size: 11px;
  color: var(--text-secondary);
}
.class-detail-trigger {
  margin-left: auto;
  white-space: nowrap;
}
.class-file-picker {
  margin-top: 8px;
  border-top: 1px solid var(--line);
  padding-top: 8px;
  font-size: 11px;
}
.class-file-picker summary {
  cursor: pointer;
}
.class-file-picker summary span {
  color: var(--text-secondary);
  margin-left: 8px;
}
.class-file-mode {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 10px;
  font-size: 12px;
}
.class-file-mode input {
  width: 15px;
  height: 15px;
  accent-color: var(--accent);
}
.class-file-options {
  grid-template-columns: minmax(0, 1fr);
}
.class-file-options label {
  font-family: var(--font-mono, monospace);
  font-size: 11px;
}
.class-scope-note {
  font-size: 10px;
  color: var(--text-secondary);
  margin: 7px 0 0;
}
.passport-scope-action {
  width: 100%;
  margin-top: 10px;
}
.architecture-class-detail {
  margin: 0 22px 12px;
  min-width: 0;
}
.class-detail-heading {
  flex-wrap: wrap;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.class-detail-heading > div {
  flex: 1;
  min-width: 150px;
}
.class-detail-heading h3 {
  margin: 0;
  font-size: 16px;
}
.class-detail-heading p {
  margin: 3px 0 0;
  color: var(--text-secondary);
  font-size: 11px;
  overflow-wrap: anywhere;
}
.class-detail-tabs {
  display: flex;
  gap: 8px;
  margin: 10px 0 0;
  flex-wrap: wrap;
}
.class-detail-tabs button {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 10px;
  background: white;
  font-size: 12px;
  color: var(--text-secondary);
}
.class-detail-tabs button[aria-pressed='true'] {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--canvas);
}
.class-design-selector {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 12px;
  margin-bottom: 10px;
}
.class-design-selector select {
  width: auto;
  max-width: 100%;
}
.class-detail-disclaimer {
  padding: 8px 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
}
.class-detail-disclaimer summary {
  cursor: pointer;
}
.class-detail-disclaimer p {
  margin: 8px 0 0;
}
.class-detail-message {
  font-size: 12px;
  color: var(--text-secondary);
}
.class-detail-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 220px;
  padding: 24px;
  text-align: center;
  border: 1px dashed var(--line);
  border-radius: 8px;
}
.class-detail-state h3 {
  font-size: 15px;
  margin: 0;
}
.class-detail-state p {
  max-width: 540px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
  margin: 0;
  overflow-wrap: anywhere;
}
.class-detail-evidence {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin: 12px 0;
  padding: 12px;
  background: var(--canvas);
  border-radius: 8px;
  font-size: 12px;
}
.class-detail-evidence h4 {
  margin: 0 0 7px;
  font-size: 12px;
}
.class-detail-evidence ul {
  padding-left: 18px;
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.class-detail-files {
  padding: 10px 0;
  font-size: 12px;
}
.class-detail-files summary {
  cursor: pointer;
  margin-bottom: 10px;
}
.class-detail-spinner {
  animation: detail-spin 1s linear infinite;
  color: var(--accent);
}
@keyframes detail-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .class-detail-spinner {
    animation: none;
  }
}
@media (max-width: 700px) {
  .class-scope,
  .architecture-class-detail {
    margin-left: 14px;
    margin-right: 14px;
  }
  .class-scope-chips {
    flex-basis: 100%;
    order: 2;
  }
  .class-detail-heading > div {
    order: -1;
    flex-basis: 100%;
  }
  .class-detail-heading > .icon-button {
    margin-left: auto;
  }
  .class-detail-evidence {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
