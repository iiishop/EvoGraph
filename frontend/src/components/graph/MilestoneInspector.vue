<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { X } from 'lucide-vue-next';
import type { Milestone, Project } from '../../types';
import { acceptanceScope, acceptanceSummary } from '../../lib/acceptance';
import { useWorkspace } from '../../composables/useWorkspace';
import StatusBadge from '../ui/StatusBadge.vue';
import AssetPreview from '../attachments/AssetPreview.vue';
import DiagramView from '../design/DiagramView.vue';
import UmlView from '../design/UmlView.vue';
import TaskWorkflow from './TaskWorkflow.vue';
const props = defineProps<{ milestone: Milestone; project: Project }>();
const { selectNode } = useWorkspace();
const tab = ref('design');
watch(
  () => props.milestone.id,
  () => {
    tab.value = 'design';
  },
);
const behaviors = computed(() =>
  props.project.behaviors.filter((b) => props.milestone.behavior_revision_ids.includes(b.id)),
);
const acceptance = computed(() => acceptanceSummary(props.project));
const targetCount = computed(
  () => behaviors.value.filter((behavior) => acceptance.value.targetIds.has(behavior.id)).length,
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
      <h2 :title="milestone.title">{{ milestone.title }}</h2>
      <StatusBadge :status="milestone.status" />
    </div>
    <nav class="detail-tabs" aria-label="任务详情">
      <button
        v-for="item in [
          { id: 'design', label: '交付规划' },
          { id: 'flow', label: '执行与验收' },
          { id: 'history', label: '记录' },
        ]"
        :key="item.id"
        :aria-pressed="tab === item.id"
        @click="tab = item.id"
      >
        {{ item.label }}
      </button>
    </nav>
    <div :key="`${milestone.id}-${tab}`" class="inspector-scroll">
      <div class="inspector-panel">
        <p v-if="tab === 'design'" class="planning-intro">
          先明确这一步交付什么，再查看前置依赖与验收标准。
        </p>
        <details class="inspector-intent">
          <summary>展开完整说明</summary>
          <strong>{{ milestone.title }}</strong>
          <p>{{ milestone.intent }}</p>
        </details>
        <TaskWorkflow v-if="tab === 'flow'" :project="project" :milestone="milestone" />
        <template v-else-if="tab === 'design'">
          <section>
            <h3>交付范围</h3>
            <code v-for="scope in milestone.scope" :key="scope" class="scope-path">{{
              scope
            }}</code>
          </section>
          <section>
            <h3 class="acceptance-heading">
              验收标准
              <span>计入最终目标 {{ targetCount }} / 本步共 {{ behaviors.length }} 项</span>
            </h3>
            <p class="acceptance-explanation">
              最终目标要求持续成立；阶段检查仅约束本步。两类都必须通过本步验收。
            </p>
            <p v-if="!behaviors.length" class="muted">尚未定义本步验收标准。</p>
            <ol v-else class="behavior-contract">
              <li v-for="b in behaviors" :key="b.id">
                <p>{{ b.statement }}</p>
                <div class="behavior-meta">
                  <span class="acceptance-scope" :class="acceptanceScope(b)">
                    {{ acceptanceScope(b) === 'milestone' ? '阶段检查' : '最终目标要求' }}
                  </span>
                  <small>{{ b.behavior_key }} · v{{ b.version }}</small>
                </div>
                <small
                  v-if="acceptanceScope(b) === 'target' && !acceptance.targetIds.has(b.id)"
                  class="acceptance-excluded"
                >
                  未纳入当前目标版本
                </small>
              </li>
            </ol>
          </section>
          <section class="planning-dependencies">
            <h3>
              前置依赖 <span>{{ milestone.dependencies.length }}</span>
            </h3>
            <p v-if="!milestone.dependencies.length" class="muted">
              没有前置里程碑，可独立安排这一步。
            </p>
            <article v-for="id in milestone.dependencies" :key="id">
              <button class="dependency-link" @click="selectNode(id)">
                <small>{{ id }}</small>
                <strong>{{
                  [...project.milestones, ...project.source_milestones].find((m) => m.id === id)
                    ?.title ?? id
                }}</strong>
              </button>
              <p>{{ milestone.dependency_reasons[id] }}</p>
            </article>
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
          <section v-else-if="project.architectures?.length">
            <h3>组件待关联</h3>
            <p class="muted">
              此步骤尚未关联现有架构组件。可以先完善交付规划，后续再补充映射；执行与验收要求仍然保留。
            </p>
          </section>
          <section
            v-for="diagram in project.diagrams.filter((d) =>
              d.milestone_ids.includes(milestone.id),
            )"
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
    </div>
  </aside>
</template>
