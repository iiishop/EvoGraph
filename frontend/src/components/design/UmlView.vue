<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import type { UmlDiagram } from '../../types';
import { command } from '../../api/client';
import DiagramImage from './DiagramImage.vue';
import AppModal from '../ui/AppModal.vue';
const props = defineProps<{ diagram: UmlDiagram; projectId: string }>();
const image = ref(''),
  error = ref(''),
  loading = ref(false),
  expanded = ref(false);
let sequence = 0;
const kind = computed(
  () =>
    ({ class: '类图', sequence: '时序图', activity: '流程图', state: '状态图' })[
      props.diagram.kind
    ],
);
watch(
  () => [props.projectId, props.diagram.id, props.diagram.revision],
  async () => {
    const current = ++sequence;
    image.value = '';
    error.value = '';
    loading.value = true;
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
  <article class="uml-view">
    <header>
      <div>
        <strong>{{ diagram.title }}</strong
        ><small
          >{{ kind }} · v{{ diagram.revision }} ·
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
        <button class="button secondary" :disabled="!image" @click="expanded = true">
          展开大图</button
        ><button class="button secondary" @click="download">导出 .puml</button>
      </div>
    </header>
    <p class="muted">{{ diagram.scope }}</p>
    <p v-if="diagram.design_elements?.length" class="muted">
      DESIGN 待实现：{{ diagram.design_elements.join('、') }}
    </p>
    <p v-if="loading" role="status">正在本地编译 UML…</p>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <DiagramImage v-if="image" :src="image" :title="diagram.title" />
    <details>
      <summary>PlantUML 源码</summary>
      <pre class="uml-source">{{ diagram.source }}</pre>
    </details>
  </article>
  <AppModal v-if="expanded" :title="diagram.title" wide class="uml-modal" @close="expanded = false"
    ><DiagramImage :src="image" :title="diagram.title" expanded
  /></AppModal>
</template>
