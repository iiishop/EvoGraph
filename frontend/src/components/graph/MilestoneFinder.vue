<script setup lang="ts">
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue';
import AppModal from '../ui/AppModal.vue';
import { findMilestones } from '../../lib/milestoneFinder';
import type { Milestone } from '../../types';

const props = defineProps<{ milestones: Milestone[] }>();
const emit = defineEmits<{ select: [id: string]; close: [] }>();
const query = ref('');
const input = ref<HTMLInputElement>();
const list = ref<HTMLElement>();
const listId = `milestone-finder-${useId()}`;
const visible = computed(() => findMilestones(props.milestones, query.value));
const activeId = ref('');
const activeIndex = computed(() => visible.value.findIndex((item) => item.id === activeId.value));
watch(
  () => visible.value.map((item) => item.id).join('\0'),
  () => {
    if (!visible.value.some((item) => item.id === activeId.value))
      activeId.value = visible.value[0]?.id ?? '';
  },
  { immediate: true, flush: 'sync' },
);
watch(
  query,
  () => {
    activeId.value = visible.value[0]?.id ?? '';
  },
  { flush: 'sync' },
);
watch(activeId, async () => {
  await nextTick();
  list.value?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
});
onMounted(async () => {
  await nextTick();
  input.value?.focus();
});

function choose(id: string) {
  // Validate against live results, not the row that existed when a click began.
  if (visible.value.some((item) => item.id === id)) emit('select', id);
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    emit('close');
  } else if (event.key === 'Enter') {
    event.preventDefault();
    choose(activeId.value);
  } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const count = visible.value.length;
    if (count)
      activeId.value =
        visible.value[
          (activeIndex.value + (event.key === 'ArrowDown' ? 1 : -1) + count) % count
        ].id;
  }
}
</script>
<template>
  <AppModal title="查找里程碑" aria-label="查找里程碑" @close="emit('close')">
    <div class="milestone-finder">
      <label :for="`${listId}-input`">按 ID、标题或范围定位</label>
      <input
        :id="`${listId}-input`"
        ref="input"
        v-model="query"
        type="search"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="true"
        :aria-controls="listId"
        :aria-activedescendant="activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined"
        autocomplete="off"
        placeholder="例如 M12、登录或 src/auth"
        @keydown="keydown"
      />
      <p class="finder-count" role="status">
        {{ visible.length }} 个匹配 · ↑↓ 选择 · Enter 定位 · Esc 返回
      </p>
      <div :id="listId" ref="list" class="finder-results" role="listbox" aria-label="匹配的里程碑">
        <button
          v-for="(item, index) in visible"
          :id="`${listId}-${index}`"
          :key="item.id"
          type="button"
          role="option"
          :aria-selected="item.id === activeId"
          :class="{ active: item.id === activeId }"
          tabindex="-1"
          @mousedown.prevent
          @click="choose(item.id)"
        >
          <span class="finder-result-heading"
            ><strong>{{ item.id }}</strong
            ><small :class="{ source: item.origin === 'source' }">{{
              item.origin === 'source' ? 'SRC · 源码观察' : '计划里程碑'
            }}</small></span
          >
          <span class="finder-result-title">{{ item.title }}</span>
          <small class="finder-result-scope">{{ item.scope.join(' · ') || '尚未填写范围' }}</small>
        </button>
      </div>
      <p v-if="!visible.length" class="finder-empty">
        没有匹配的里程碑。试试更短的 ID、标题关键词或文件目录；SRC 也可以搜索。
      </p>
    </div>
  </AppModal>
</template>
<style scoped>
.milestone-finder {
  padding: 0 20px 20px;
}
.milestone-finder label {
  display: block;
  font-size: 12px;
  margin-bottom: 8px;
}
.milestone-finder input {
  width: 100%;
}
.finder-count,
.finder-empty {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
}
.finder-results {
  max-height: min(46vh, 420px);
  overflow: auto;
  scrollbar-gutter: stable;
}
.finder-results button {
  display: grid;
  gap: 5px;
  width: 100%;
  min-width: 0;
  padding: 11px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  text-align: left;
}
.finder-results button.active,
.finder-results button:hover {
  background: #eaf0f7;
  border-color: #c6d4e2;
}
.finder-result-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  font-size: 12px;
}
.finder-result-heading strong {
  min-width: 0;
  overflow-wrap: anywhere;
}
.finder-result-heading small {
  color: var(--text-secondary);
  font-size: 10px;
}
.finder-result-heading small.source {
  color: #477476;
}
.finder-result-title {
  font-size: 13px;
  overflow-wrap: anywhere;
}
.finder-result-scope {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
