<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { FileText, FolderSearch } from 'lucide-vue-next';
import { fuzzyMatch } from '../../lib/referenceMatch';
import type { ReferenceItem } from '../../types';

const props = defineProps<{
  items: ReferenceItem[];
  query: string;
}>();
const emit = defineEmits<{ select: [item: ReferenceItem] }>();

const visible = computed(() =>
  props.items.filter((item) => fuzzyMatch(`${item.label} ${item.detail}`, props.query)),
);
const activeIndex = ref(0);
const panel = ref<HTMLElement>();
watch(activeIndex, async () => {
  await nextTick();
  panel.value?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
});
watch(
  () => [props.query, props.items.length],
  () => {
    activeIndex.value = 0;
  },
);

function move(delta: number) {
  if (!visible.value.length) return;
  activeIndex.value = (activeIndex.value + delta + visible.value.length) % visible.value.length;
}

function chooseActive() {
  const item = visible.value[activeIndex.value];
  if (item) emit('select', item);
}

defineExpose({ move, chooseActive });
</script>

<template>
  <div ref="panel" class="reference-mention-picker" role="listbox" aria-label="引用项目资料">
    <div class="reference-mention-heading">
      <strong>@ 引用资料</strong><small>{{ visible.length }} 个匹配</small>
    </div>
    <button
      v-for="(item, index) in visible"
      :key="item.id"
      type="button"
      role="option"
      :aria-selected="index === activeIndex"
      :class="{ active: index === activeIndex }"
      @mousedown.prevent
      @click="emit('select', item)"
    >
      <FileText v-if="item.kind === 'attachment'" :size="15" aria-hidden="true" />
      <FolderSearch v-else :size="15" aria-hidden="true" />
      <span
        ><strong>{{ item.label }}</strong
        ><small>{{ item.detail }}</small></span
      >
    </button>
    <p v-if="!visible.length">没有匹配的项目资料或仓库文件</p>
  </div>
</template>

<style scoped>
.reference-mention-picker {
  position: absolute;
  bottom: 100%;
  left: 0;
  width: min(560px, 100%);
  max-height: 320px;
  overflow: auto;
  z-index: 30;
  background: var(--surface, white);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: 0 12px 32px #20324b22;
  padding: 6px;
}
.reference-mention-heading {
  display: flex;
  justify-content: space-between;
  padding: 10px;
  color: var(--text-secondary);
  font-size: 12px;
}
.reference-mention-picker button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--ink);
  text-align: left;
}
.reference-mention-picker button.active,
.reference-mention-picker button:hover {
  background: #eaf0f7;
}
.reference-mention-picker button span {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.reference-mention-picker button strong {
  font-size: 13px;
  overflow-wrap: anywhere;
  font-weight: 500;
}
.reference-mention-picker small,
.reference-mention-picker p {
  color: var(--text-secondary);
  font-size: 11px;
}
</style>
