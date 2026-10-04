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
import DeliveryBriefDialog from './DeliveryBriefDialog.vue';
import AgentTurnSummary from '../agent/AgentTurnSummary.vue';
import { milestoneTurnHistory, dependencyTypeLabels } from '../../lib/turnSummary';
const props = defineProps<{ milestone: Milestone; project: Project }>();
const { selectNode } = useWorkspace();
const emit = defineEmits<{ locate: [id: string] }>();
const workflowOpen = ref(false);
const briefOpen = ref(false);
watch(
  () => [props.project.id, props.milestone.id],
  () => {
    briefOpen.value = false;
  },
);
const allMilestones = computed(() => [
  ...props.project.milestones,
  ...(props.project.source_milestones ?? []),
]);
const history = computed(() => milestoneTurnHistory(props.project, props.milestone.id));
const preview = computed(() =>
  props.milestone.intent.length > 180
    ? `${props.milestone.intent.slice(0, 180)}…`
    : props.milestone.intent,
);
const dependency = (id: string) => allMilestones.value.find((item) => item.id === id);
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
  <aside class="inspector explanatory-inspector" aria-label="里程碑详情">
    <header>
      <span>{{ milestone.id }}</span
      ><button class="icon-button" aria-label="关闭节点详情" @click="selectNode(null)">
        <X :size="18" />
      </button>
    </header>
    <div class="inspector-scroll" tabindex="0" aria-label="里程碑完整说明">
      <div class="inspector-panel">
        <div class="inspector-title">
          <h2>{{ milestone.title }}</h2>
          <StatusBadge :status="milestone.status" />
        </div>
        <p class="inspector-outcome">{{ preview || '尚未补充交付说明。' }}</p>
        <details class="inspector-intent">
          <summary>展开完整说明</summary>
          <strong>{{ milestone.title }}</strong>
          <p>{{ milestone.intent }}</p>
        </details>
        <div v-if="milestone.dependencies.length" class="meaning-card">
          <span class="meaning-label">为什么在这里</span>
          <p v-for="id in milestone.dependencies.slice(0, 2)" :key="id">
            <strong>{{ dependency(id)?.title || id }}</strong
            ><span>{{
              milestone.dependency_reasons[id] || '尚未记录依赖依据，可在项目对话中补充。'
            }}</span>
          </p>
          <small v-if="milestone.dependencies.length > 2"
            >其余 {{ milestone.dependencies.length - 2 }} 项见「依赖与依据」</small
          >
        </div>
        <details class="inspector-disclosure">
          <summary>
            <strong>交付约定</strong><span>范围 · {{ behaviors.length }} 项验收</span>
          </summary>
          <div class="disclosure-content">
            <button class="button secondary" @click="briefOpen = true">查看完整交付说明</button>
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
            <AssetPreview
              v-for="asset in project.attachments.filter((a) =>
                milestone.attachment_ids.includes(a.id),
              )"
              :key="asset.id"
              :asset="asset"
              :project-id="project.id"
            />
          </div>
        </details>
        <details class="inspector-disclosure">
          <summary>
            <strong>依赖与依据</strong><span>{{ milestone.dependencies.length }} 项前置</span>
          </summary>
          <div class="disclosure-content">
            <section class="planning-dependencies">
              <h3>
                前置依赖 <span>{{ milestone.dependencies.length }}</span>
              </h3>
              <p v-if="!milestone.dependencies.length" class="muted">
                没有前置里程碑，可独立安排这一步。
              </p>
              <article v-for="id in milestone.dependencies" :key="id">
                <button class="dependency-link" @click="$emit('locate', id)">
                  <small
                    >{{ id }} ·
                    {{
                      dependencyTypeLabels[milestone.dependency_types?.[id]] || '实现'
                    }}前置</small
                  >
                  <strong>{{
                    [...project.milestones, ...project.source_milestones].find((m) => m.id === id)
                      ?.title ?? id
                  }}</strong>
                </button>
                <p>{{ milestone.dependency_reasons[id] }}</p>
              </article>
            </section>

            <section v-if="milestone.obligations?.length">
              <h3>调查依据</h3>
              <article v-for="item in milestone.obligations" :key="item.id">
                <strong>{{ item.label }}</strong
                ><small>{{ item.resolved ? '已记录依据' : '待调查' }}</small>
                <p>{{ item.note || '尚未记录依据。' }}</p>
              </article>
            </section>
            <section>
              <h3>资源</h3>
              <p>{{ milestone.resources?.join('、') || '未声明独立资源' }}</p>
            </section>
          </div>
        </details>
        <details class="inspector-disclosure">
          <summary>
            <strong>架构影响</strong
            ><span>{{ milestone.architecture_components.length }} 个组件</span>
          </summary>
          <div class="disclosure-content">
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

            <p
              v-if="!milestone.architecture_components.length && !project.architectures?.length"
              class="muted"
            >
              尚未记录架构关联，不据此推断运行时影响。
            </p>
          </div>
        </details>
        <details class="inspector-disclosure">
          <summary>
            <strong>变更记录</strong><span>{{ history.length }} 轮相关变化</span>
          </summary>
          <div class="disclosure-content">
            <p v-if="!history.length" class="muted">暂无明确关联此节点的已保存变更记录。</p>
            <article v-for="item in history" :key="item.id" class="node-change-record">
              <time>{{ new Date(item.createdAt).toLocaleString() }}</time
              ><AgentTurnSummary
                :summary="item.summary"
                :milestones="allMilestones"
                @locate="$emit('locate', $event)"
              />
            </article>
          </div>
        </details>
        <details
          class="inspector-disclosure inspector-execution"
          @toggle="workflowOpen = ($event.target as HTMLDetailsElement).open"
        >
          <summary>
            <strong>执行与证据</strong><span>{{ evidence.length }} 份报告</span>
          </summary>
          <div class="disclosure-content">
            <TaskWorkflow v-if="workflowOpen" :project="project" :milestone="milestone" />
            <section>
              <h3>验收记录</h3>
              <p v-if="!evidence.length" class="muted">验收报告会保存在这里。</p>
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
              </article>
            </section>
          </div>
        </details>
        <p class="inspector-composer-note">继续在项目对话中补充想法，由 Agent 判断影响范围。</p>
      </div>
    </div>
    <DeliveryBriefDialog
      v-if="briefOpen"
      :project="project"
      :milestone="milestone"
      @close="briefOpen = false"
      @locate="
        (id) => {
          briefOpen = false;
          emit('locate', id);
        }
      "
    />
  </aside>
</template>
