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
      <span class="source-badge">SRC 已实现里程碑</span
      ><button class="icon-button" aria-label="关闭源码详情" @click="selectNode(null)">
        <X :size="18" />
      </button>
    </header>
    <div class="inspector-scroll">
      <h2>{{ milestone.title }}</h2>
      <p class="intent">{{ milestone.intent }}</p>
      <p v-if="milestone.source_baseline_id !== project.baselines.at(-1)?.id" class="muted">
        基线已变化，此里程碑依据旧基线推导，等待重新调查。
      </p>
      <section>
        <h4>交付范围</h4>
        <code v-for="path in milestone.scope" :key="path" class="scope-path">{{ path }}</code>
      </section>
      <section>
        <h4>已实现的行为契约</h4>
        <article
          v-for="behavior in milestone.source_behaviors"
          :key="behavior.key"
          class="source-contract"
        >
          <p>{{ behavior.statement }}</p>
          <code v-for="path in behavior.source_refs" :key="path" class="scope-path">{{
            path
          }}</code>
        </article>
      </section>
      <section v-if="milestone.dependencies.length">
        <h4>前置能力</h4>
        <article v-for="id in milestone.dependencies" :key="id" class="source-contract">
          <button class="button secondary" @click="selectNode(id)">
            {{ project.source_milestones.find((m) => m.id === id)?.title ?? id }}
          </button>
          <p>{{ milestone.dependency_reasons[id] }}</p>
        </article>
      </section>
      <section>
        <h4><FileCode :size="16" />源码依据</h4>
        <code v-for="path in milestone.source_refs" :key="path" class="scope-path">{{ path }}</code>
      </section>
      <p class="muted">
        由 Agent 根据源码倒推的已实现交付能力，不代表真实历史 PR 或已通过独立验收。
        后续改动可在下方交给 Agent 规划，不重复计入待交付任务。
      </p>
      <small
        >推导依据：基线 B{{
          project.baselines.find((b) => b.id === milestone.source_baseline_id)?.number
        }}</small
      >
      <p class="muted">{{ project.source_analysis_summary }}</p>
    </div>
  </aside>
</template>
<style scoped>
.source-contract {
  padding: 10px 0;
  border-bottom: 1px solid var(--line, #d6dfe4);
}
.source-contract p {
  margin: 0 0 8px;
  line-height: 1.65;
}
</style>
