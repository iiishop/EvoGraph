<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { X } from 'lucide-vue-next';
import type { Milestone, Project } from '../../types';
import { useWorkspace } from '../../composables/useWorkspace';
import StatusBadge from '../ui/StatusBadge.vue';
import AssetPreview from '../attachments/AssetPreview.vue';
import DiagramView from '../design/DiagramView.vue';
import UmlView from '../design/UmlView.vue';
import TaskWorkflow from './TaskWorkflow.vue';
const props = defineProps<{ milestone: Milestone; project: Project }>();
const { selectNode } = useWorkspace();
const tab = ref('flow');
watch(
  () => props.milestone.id,
  () => {
    tab.value = 'flow';
  },
);
const behaviors = computed(() =>
  props.project.behaviors.filter((b) => props.milestone.behavior_revision_ids.includes(b.id)),
);
const evidence = computed(() =>
  props.project.evidence
    .filter((e) => e.milestone_id === props.milestone.id)
    .slice()
    .reverse(),
);
const uml = computed(() =>
  [...new Map((props.project.uml_diagrams ?? []).map((d) => [d.id, d])).values()].filter((d) =>
    d.milestone_ids.includes(props.milestone.id),
  ),
);
</script>
<template>
  <aside class="inspector">
    <header>
      <span>{{ milestone.id }}</span
      ><button class="icon-button" aria-label="关闭节点详情" @click="selectNode(null)">
        <X :size="18" />
      </button>
    </header>
    <div class="inspector-title">
      <h2>{{ milestone.title }}</h2>
      <StatusBadge :status="milestone.status" />
      <p>{{ milestone.intent }}</p>
    </div>
    <nav class="detail-tabs" aria-label="任务详情">
      <button
        v-for="item in [
          { id: 'flow', label: '任务流程' },
          { id: 'design', label: '范围与设计' },
          { id: 'history', label: '记录' },
        ]"
        :key="item.id"
        :aria-pressed="tab === item.id"
        @click="tab = item.id"
      >
        {{ item.label }}
      </button>
    </nav>
    <div class="inspector-scroll">
      <TaskWorkflow v-if="tab === 'flow'" :project="project" :milestone="milestone" />
      <template v-else-if="tab === 'design'">
        <section>
          <h3>交付范围</h3>
          <code v-for="scope in milestone.scope" :key="scope" class="scope-path">{{ scope }}</code>
        </section>
        <section>
          <h3>行为要求</h3>
          <ol class="behavior-contract">
            <li v-for="b in behaviors" :key="b.id">
              <p>{{ b.statement }}</p>
              <small>{{ b.behavior_key }} · v{{ b.version }}</small>
            </li>
          </ol>
        </section>
        <section v-if="milestone.migration_steps?.length">
          <h3>迁移与退役步骤</h3>
          <article v-for="step in milestone.migration_steps" :key="step.component_id">
            <strong
              >{{ step.component_id }} <small>原架构 A{{ step.from_revision }}</small></strong
            >
            <p>{{ step.instruction }}</p>
          </article>
        </section>
        <section v-if="milestone.architecture_components.length">
          <h3>关联组件</h3>
          <code v-for="id in milestone.architecture_components" :key="id" class="scope-path">{{
            id
          }}</code>
        </section>
        <section
          v-for="diagram in project.diagrams.filter((d) => d.milestone_ids.includes(milestone.id))"
          :key="diagram.id"
        >
          <h3>{{ diagram.title }}</h3>
          <DiagramView :diagram="diagram" />
        </section>
        <UmlView
          v-for="diagram in uml"
          :key="diagram.id"
          :diagram="diagram"
          :project-id="project.id"
        />
        <AssetPreview
          v-for="asset in project.attachments.filter((a) =>
            milestone.attachment_ids.includes(a.id),
          )"
          :key="asset.id"
          :asset="asset"
          :project-id="project.id"
        />
      </template>
      <template v-else
        ><p v-if="!evidence.length" class="muted">验收报告会保存在这里。</p>
        <article v-for="item in evidence" :key="item.id" class="evidence-receipt">
          <header>
            <StatusBadge :status="item.result" /><time>{{
              new Date(item.created_at).toLocaleString()
            }}</time>
          </header>
          <p>{{ project.evidence_validity[item.id] }}</p>
          <details>
            <summary>查看验收报告</summary>
            <pre>{{ item.output }}</pre>
          </details>
        </article></template
      >
    </div>
  </aside>
</template>
