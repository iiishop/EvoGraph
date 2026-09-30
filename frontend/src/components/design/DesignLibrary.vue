<script setup lang="ts">
import type { Project } from '../../types';
import DiagramView from './DiagramView.vue';
import AssetPreview from '../attachments/AssetPreview.vue';
import { computed, nextTick, ref, watch } from 'vue';
import { useAgent } from '../../composables/useAgent';
import UmlView from './UmlView.vue';
const agent = useAgent();
const root = ref<HTMLElement>();
const props = defineProps<{ project: Project }>();
const diagrams = computed(() => props.project.diagrams.filter((d) => d.kind !== 'class'));
const uml = computed(() => [
  ...new Map(
    (props.project.uml_diagrams ?? []).filter((d) => d.kind !== 'class').map((d) => [d.id, d]),
  ).values(),
]);
watch(
  () => [agent.state.navigationTick, agent.state.pulse],
  async () => {
    if (
      agent.state.projectId !== props.project.id ||
      agent.state.follow[props.project.id] === false ||
      agent.state.view !== 'design'
    )
      return;
    await nextTick();
    root.value
      ?.querySelector(`[data-diagram="${CSS.escape(agent.state.diagramId)}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  },
  { immediate: true },
);
</script>
<template>
  <section ref="root" class="design-panel">
    <div class="design-heading">
      <div>
        <small>REFERENCES / 文档、图片与设计图</small>
        <h2>项目资料</h2>
      </div>
    </div>
    <p class="design-summary">
      在下方添加文档或图片，选择后随建议发送。也可以让 Agent 绘制状态机、流程图或 UI 结构图。
    </p>
    <article
      v-for="diagram in diagrams"
      :key="diagram.id"
      :data-diagram="diagram.id"
      class="diagram-card"
    >
      <header>
        <h3>{{ diagram.title }}</h3>
        <span>{{ diagram.kind }} · {{ diagram.milestone_ids.join(' / ') }}</span>
      </header>
      <DiagramView :diagram="diagram" />
      <div class="diagram-reference-links">
        <a
          v-for="asset in project.attachments.filter((a) => diagram.attachment_ids.includes(a.id))"
          :key="asset.id"
          :href="`#project-asset-${asset.id}`"
          >引用资料：{{ asset.name }}</a
        >
      </div>
    </article>
    <UmlView
      v-for="diagram in uml"
      :key="diagram.id"
      :data-diagram="diagram.id"
      :diagram="diagram"
      :project-id="project.id"
    />
    <div class="asset-grid">
      <AssetPreview
        v-for="asset in project.attachments"
        :id="`project-asset-${asset.id}`"
        :key="asset.id"
        :asset="asset"
        :project-id="project.id"
      />
    </div>
    <div v-if="!project.attachments.length && !diagrams.length && !uml.length" class="empty-state">
      <h3>把想法变成可参考的资料</h3>
      <p>支持文档提取、图片预览，以及随项目长期维护的结构化设计图。</p>
    </div>
  </section>
</template>

<style scoped>
.diagram-reference-links {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 10px 0;
}
.diagram-reference-links a {
  font-size: 12px;
  color: var(--accent);
}
</style>
