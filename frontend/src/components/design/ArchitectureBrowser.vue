<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue';
import { Search, X, ChevronDown } from 'lucide-vue-next';
import { findArchitectureNodes } from '../../lib/architectureBrowse';
import { architectureRole } from '../../lib/architectureRoles';
import type { Diagram } from '../../types';

const props = defineProps<{
  nodes: Diagram['nodes'];
  query: string;
  role: string;
  focusedId: string;
}>();
const emit = defineEmits<{ 'update:query': [value: string]; select: [id: string] }>();
const expanded = ref(false);
const input = ref<HTMLInputElement>();
const list = ref<HTMLElement>();
const listId = `architecture-browser-${useId()}`;
const results = computed(() => findArchitectureNodes(props.nodes, props.query, props.role));
const activeId = ref('');
const activeIndex = computed(() => results.value.findIndex((node) => node.id === activeId.value));
watch(
  () => results.value.map((node) => node.id).join('\0'),
  () => {
    if (!results.value.some((node) => node.id === activeId.value))
      activeId.value = results.value[0]?.id ?? '';
  },
  { immediate: true, flush: 'sync' },
);
watch(
  () => [props.query, props.role],
  () => {
    activeId.value = results.value[0]?.id ?? '';
  },
  { flush: 'sync' },
);
watch(activeId, async () => {
  await nextTick();
  if (expanded.value)
    list.value
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
});
function choose(id: string) {
  if (!results.value.some((node) => node.id === id)) return;
  emit('select', id);
  expanded.value = false;
  input.value?.focus();
}
function toggle() {
  expanded.value = !expanded.value;
  if (expanded.value) input.value?.focus();
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === 'Escape') {
    if (expanded.value) {
      event.preventDefault();
      event.stopPropagation();
      expanded.value = false;
    }
  } else if (event.key === 'Enter') {
    event.preventDefault();
    if (expanded.value) choose(activeId.value);
    else expanded.value = true;
  } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (!expanded.value) {
      expanded.value = true;
      return;
    }
    const count = results.value.length;
    if (count)
      activeId.value =
        results.value[
          (activeIndex.value + (event.key === 'ArrowDown' ? 1 : -1) + count) % count
        ].id;
  }
}
function leave(event: FocusEvent) {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null))
    expanded.value = false;
}
function clear() {
  emit('update:query', '');
  expanded.value = true;
  input.value?.focus();
}
</script>
<template>
  <div class="architecture-browser" @focusout="leave">
    <div class="component-search">
      <Search :size="16" aria-hidden="true" />
      <input
        :id="`${listId}-input`"
        ref="input"
        :value="query"
        type="search"
        role="combobox"
        aria-label="搜索架构组件"
        aria-autocomplete="list"
        :aria-expanded="expanded"
        :aria-controls="expanded ? listId : undefined"
        :aria-activedescendant="
          expanded && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined
        "
        :aria-describedby="`${listId}-help`"
        autocomplete="off"
        placeholder="搜索名称、职责或源码路径"
        @input="
          emit('update:query', ($event.target as HTMLInputElement).value);
          expanded = true;
        "
        @keydown="keydown"
      />
      <button
        v-if="query"
        type="button"
        class="icon-button"
        aria-label="清空组件搜索"
        @click="clear"
      >
        <X :size="14" />
      </button>
      <button
        type="button"
        class="icon-button"
        aria-label="浏览架构组件"
        :aria-expanded="expanded"
        @click="toggle"
      >
        <ChevronDown :size="15" />
      </button>
    </div>
    <span :id="`${listId}-help`" class="sr-only"
      >输入关键词筛选；下箭头浏览，Enter 定位，Esc 收起</span
    >
    <div v-if="expanded" class="architecture-browser-popover">
      <p class="architecture-browser-count" role="status">
        {{ results.length }} 个匹配 · ↑↓ 选择 · Enter 定位 · Esc 收起
      </p>
      <div
        :id="listId"
        ref="list"
        class="architecture-browser-results"
        role="listbox"
        aria-label="匹配的架构组件"
      >
        <button
          v-for="(node, index) in results"
          :id="`${listId}-${index}`"
          :key="node.id"
          type="button"
          role="option"
          :aria-selected="node.id === activeId"
          :class="{ active: node.id === activeId }"
          tabindex="-1"
          @mousedown.prevent
          @click="choose(node.id)"
        >
          <span class="architecture-browser-name"
            ><strong>{{ node.label }}</strong
            ><small
              >{{ architectureRole(node.role).label
              }}{{ node.id === focusedId ? ' · 已定位' : '' }}</small
            ></span
          >
          <span class="architecture-browser-description">{{
            node.description || '尚未填写职责'
          }}</span>
          <small class="architecture-browser-path"
            >{{ node.id }}{{ node.source_refs?.length ? ` · ${node.source_refs[0]}` : '' }}</small
          >
        </button>
      </div>
      <p v-if="!results.length" class="architecture-browser-empty">
        没有匹配组件。试试更短的名称、职责或文件路径，或切回全部类型。
      </p>
    </div>
  </div>
</template>
<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.architecture-browser {
  position: relative;
  flex: 1 1 280px;
  min-width: 180px;
}
.component-search {
  gap: 6px;
}
.component-search input {
  width: 100%;
  padding-right: 0;
}
.component-search input::-webkit-search-cancel-button {
  display: none;
}
.component-search .icon-button {
  flex-shrink: 0;
  width: 29px;
  height: 30px;
}
.architecture-browser-popover {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: max(100%, 330px);
  max-width: min(520px, calc(100vw - 70px));
  z-index: 20;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel, white);
  box-shadow: 0 12px 30px #15243b1f;
  overflow: hidden;
}
.architecture-browser-count,
.architecture-browser-empty {
  margin: 0;
  padding: 10px 12px;
  font-size: 11px;
  color: var(--text-secondary);
  line-height: 1.6;
}
.architecture-browser-results {
  max-height: min(34vh, 260px);
  overflow: auto;
  overscroll-behavior: contain;
}
.architecture-browser-results button {
  display: grid;
  gap: 5px;
  width: 100%;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: 0;
  text-align: left;
  background: transparent;
  color: var(--ink);
}
.architecture-browser-results button.active,
.architecture-browser-results button:hover {
  background: #eaf0f7;
  border-color: #c6d4e2;
}
.architecture-browser-name {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}
.architecture-browser-name strong,
.architecture-browser-description,
.architecture-browser-path {
  overflow-wrap: anywhere;
}
.architecture-browser-name small {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--text-secondary);
}
.architecture-browser-description {
  font-size: 11px;
  line-height: 1.5;
}
.architecture-browser-path {
  font-size: 10px;
  color: var(--text-secondary);
}
</style>
