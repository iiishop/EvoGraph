<script setup lang="ts">
import { ref, watch, computed, nextTick, onBeforeUnmount } from 'vue';
import type { SourceClassModel, UmlDiagram } from '../../types';
import { validSourceClassModel } from '../../lib/sourceClassModel';
import SourceClassCards from './SourceClassCards.vue';
import { command } from '../../api/client';
import DiagramImage from './DiagramImage.vue';
import AppModal from '../ui/AppModal.vue';
defineOptions({ inheritAttrs: false });
const props = defineProps<{
  diagram: UmlDiagram;
  projectId: string;
  previewImage?: string;
  semantic?: SourceClassModel;
  renderStatus?: 'ready' | 'unavailable';
  renderError?: string;
  canRetryRender?: boolean;
  compact?: boolean;
}>();
const emit = defineEmits<{ retrySourceRender: [] }>();
const ephemeralSource = computed(
  () => props.diagram.origin === 'source' && props.diagram.revision === 0,
);
const displayMode = ref<'semantic' | 'standard' | 'source'>('semantic');
const sourceSelection = ref({ classId: '', memberId: '' });
const sourceModel = computed(() =>
  props.diagram.origin === 'source' &&
  props.diagram.kind === 'class' &&
  props.semantic?.project_id === props.projectId &&
  props.semantic.architecture_revision === props.diagram.architecture_revision &&
  props.semantic.baseline_id === props.diagram.baseline_id &&
  validSourceClassModel(props.semantic)
    ? props.semantic
    : undefined,
);
const image = ref(''),
  error = ref(''),
  loading = ref(false),
  expanded = ref(false);
const rawSource = ref<HTMLElement>();
let sequence = 0;
async function showSource() {
  const current = sequence;
  expanded.value = false;
  displayMode.value = 'source';
  await nextTick();
  if (current === sequence && displayMode.value === 'source') rawSource.value?.focus();
}
const kind = computed(
  () =>
    ({ class: '类图', sequence: '时序图', activity: '流程图', state: '状态图' })[
      props.diagram.kind
    ],
);
watch(
  () => [
    props.projectId,
    props.diagram.id,
    props.diagram.revision,
    props.diagram.source,
    props.diagram.origin,
    props.diagram.architecture_revision,
    props.diagram.baseline_id,
    props.previewImage,
    props.semantic,
    props.renderStatus,
    props.renderError,
  ],
  async () => {
    const current = ++sequence;
    image.value = props.renderStatus === 'unavailable' ? '' : (props.previewImage ?? '');
    error.value = props.renderError ?? '';
    expanded.value = false;
    sourceSelection.value = { classId: '', memberId: '' };
    displayMode.value = sourceModel.value ? 'semantic' : 'standard';
    // Source drill-down diagrams are ephemeral: uml.preview only looks up saved history IDs.
    const ephemeral = ephemeralSource.value;
    loading.value = !image.value && !ephemeral && !error.value;
    if (image.value) return;
    if (ephemeral || props.renderStatus === 'unavailable' || error.value) {
      error.value ||= '标准 PlantUML 图暂不可用，仍可查看已提取的成员与关系及原始图源码。';
      loading.value = false;
      return;
    }
    try {
      const result = await command<{ image: string }>('uml.preview', {
        project_id: props.projectId,
        diagram_id: props.diagram.id,
        revision: props.diagram.revision,
      });
      if (current === sequence) image.value = result.image;
    } catch (e) {
      if (current === sequence) error.value = String(e);
    } finally {
      if (current === sequence) loading.value = false;
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => sequence++);
function download() {
  const url = URL.createObjectURL(
    new Blob([props.diagram.source], { type: 'text/plain;charset=utf-8' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = `${props.diagram.id}.puml`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
</script>
<template>
  <article
    v-bind="$attrs"
    class="uml-view"
    :class="{ 'uml-compact': compact && sourceModel }"
    :aria-label="diagram.title"
  >
    <header>
      <div :class="{ 'visually-hidden': compact && sourceModel }">
        <strong>{{ diagram.title }}</strong
        ><small
          >{{ kind }} · {{ diagram.revision > 0 ? `v${diagram.revision}` : '即时提取' }} ·
          {{
            diagram.origin === 'source'
              ? 'SRC 源码依据'
              : diagram.origin === 'mixed'
                ? 'SRC + DESIGN 源码与设计'
                : 'DESIGN 设计待实现'
          }}</small
        >
      </div>
      <div class="uml-actions">
        <button
          class="button secondary"
          :disabled="!(sourceModel && displayMode === 'semantic') && !image"
          @click="expanded = true"
        >
          展开大图</button
        ><button class="button secondary" @click="download">导出 .puml</button>
      </div>
    </header>
    <p v-if="!compact || !sourceModel" class="muted">{{ diagram.scope }}</p>
    <p v-if="diagram.design_elements?.length" class="muted">
      DESIGN 待实现：{{ diagram.design_elements.join('、') }}
    </p>
    <nav v-if="sourceModel" class="uml-semantic-tabs" aria-label="类结构显示方式">
      <button :aria-pressed="displayMode === 'semantic'" @click="displayMode = 'semantic'">
        成员与关系
      </button>
      <button :aria-pressed="displayMode === 'standard'" @click="displayMode = 'standard'">
        标准 PlantUML 图
      </button>
      <button :aria-pressed="displayMode === 'source'" @click="displayMode = 'source'">
        PlantUML 源码
      </button>
    </nav>
    <p v-if="sourceModel && error" class="uml-render-notice" role="status">
      标准图暂不可用。源码声明已完成局部提取；成员、关系和 PlantUML 源码仍可查看。
    </p>
    <SourceClassCards
      v-if="sourceModel && displayMode === 'semantic'"
      :model="sourceModel"
      :selection="sourceSelection"
      @selection="sourceSelection = $event"
      @source="showSource"
    />
    <template v-if="displayMode === 'standard' || !sourceModel">
      <p v-if="loading" role="status">正在本地编译 UML…</p>
      <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      <button
        v-if="error && ephemeralSource && canRetryRender"
        class="button secondary"
        @click="emit('retrySourceRender')"
      >
        重新提取并编译
      </button>
      <DiagramImage v-if="image" :src="image" :title="diagram.title" />
    </template>
    <pre
      v-if="sourceModel && displayMode === 'source'"
      ref="rawSource"
      class="uml-source"
      tabindex="0"
      >{{ diagram.source }}</pre>
    <details v-if="!sourceModel">
      <summary>PlantUML 源码</summary>
      <pre class="uml-source">{{ diagram.source }}</pre>
    </details>
  </article>
  <AppModal
    v-if="expanded"
    :title="diagram.title"
    wide
    class="uml-modal"
    :class="{ 'semantic-modal': sourceModel && displayMode === 'semantic' }"
    @close="expanded = false"
  >
    <SourceClassCards
      v-if="sourceModel && displayMode === 'semantic'"
      :model="sourceModel"
      :selection="sourceSelection"
      @selection="sourceSelection = $event"
      @source="showSource"
    />
    <DiagramImage v-else :src="image" :title="diagram.title" expanded />
  </AppModal>
</template>

<style scoped>
.semantic-modal :deep(.source-class-scroll) {
  max-height: min(590px, calc(85dvh - 170px));
}
.semantic-modal :deep(.source-class-summary) {
  padding-top: 0;
}
.uml-compact {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0 12px;
  margin-top: 0;
  padding: 12px;
}
.uml-compact > * {
  grid-column: 1 / -1;
}
.uml-compact > header {
  grid-column: 2;
  grid-row: 1;
}
.uml-compact > .uml-semantic-tabs {
  grid-column: 1;
  grid-row: 1;
  margin: 0;
  padding: 0;
  border: 0;
  align-self: center;
}
.uml-compact .uml-actions .button {
  min-height: 30px;
  padding: 6px 9px;
  font-size: 11px;
}
@media (max-width: 760px) {
  .uml-compact > header,
  .uml-compact > .uml-semantic-tabs {
    grid-column: 1 / -1;
    grid-row: auto;
  }
}
.uml-render-notice {
  margin: 12px 0 0;
  padding: 10px 12px;
  background: #fbf6e9;
  border-radius: 8px;
  color: #8d784d;
  font-size: 11px;
  line-height: 1.8;
}
.uml-semantic-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  border-bottom: 1px solid #e2e4ee;
  margin-top: 15px;
  padding: 0 0 9px;
}
.uml-semantic-tabs button {
  border: 0;
  padding: 8px 12px;
  border-radius: 8px;
  font: inherit;
  font-size: 11px;
  background: transparent;
  color: #7a8599;
  cursor: pointer;
}
.uml-semantic-tabs button[aria-pressed='true'] {
  background: #efebf8;
  color: #756092;
}
.uml-semantic-tabs button:focus-visible {
  outline: 2px solid #9180bd;
  outline-offset: 2px;
}
</style>
