<script setup lang="ts">
import { X, FileCode } from 'lucide-vue-next';
import type { Milestone, Project } from '../../types';
import { useWorkspace } from '../../composables/useWorkspace';
defineProps<{ milestone: Milestone; project: Project }>();
const { selectNode } = useWorkspace();
</script>
<template>
  <aside class="inspector">
    <header>
      <span class="source-badge">SRC 源码现状</span
      ><button class="icon-button" aria-label="关闭源码详情" @click="selectNode(null)">
        <X :size="18" />
      </button>
    </header>
    <div class="inspector-scroll">
      <h2>{{ milestone.title }}</h2>
      <p class="intent">{{ milestone.intent }}</p>
      <section>
        <h4><FileCode :size="16" />源码依据</h4>
        <code v-for="path in milestone.source_refs" :key="path" class="scope-path">{{ path }}</code>
      </section>
      <p class="muted">
        SRC 表示基线扫描中发现的现有代码，不计入待交付 PR 和验收进度。可以在下方要求 Agent
        为这个组件规划改动。
      </p>
      <small>基线 {{ project.baselines.at(-1)?.number }}</small>
    </div>
  </aside>
</template>
