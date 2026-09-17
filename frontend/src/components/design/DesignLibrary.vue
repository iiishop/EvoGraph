<script setup lang="ts">
import type { Project } from '../../types';
import DiagramView from './DiagramView.vue';
import AssetPreview from '../attachments/AssetPreview.vue';
defineProps<{ project: Project }>();
</script>
<template>
  <section class="design-panel">
    <div class="design-heading">
      <div>
        <small>REFERENCES / 文档、图片与设计图</small>
        <h2>项目资料</h2>
      </div>
    </div>
    <p class="design-summary">
      在下方添加文档或图片，选择后随建议发送。也可以让 Agent 绘制状态机、流程图或 UI 结构图。
    </p>
    <article v-for="diagram in project.diagrams" :key="diagram.id" class="diagram-card">
      <header>
        <h3>{{ diagram.title }}</h3>
        <span>{{ diagram.kind }} · {{ diagram.milestone_ids.join(' / ') }}</span>
      </header>
      <DiagramView :diagram="diagram" />
      <div class="asset-grid">
        <AssetPreview
          v-for="asset in project.attachments.filter((a) => diagram.attachment_ids.includes(a.id))"
          :key="asset.id"
          :asset="asset"
          :project-id="project.id"
        />
      </div>
    </article>
    <div class="asset-grid">
      <AssetPreview
        v-for="asset in project.attachments"
        :key="asset.id"
        :asset="asset"
        :project-id="project.id"
      />
    </div>
    <div v-if="!project.attachments.length && !project.diagrams.length" class="empty-state">
      <h3>把想法变成可参考的资料</h3>
      <p>支持文档提取、图片预览，以及随项目长期维护的结构化设计图。</p>
    </div>
  </section>
</template>
