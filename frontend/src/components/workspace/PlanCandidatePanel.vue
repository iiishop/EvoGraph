<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { AlertCircle, Eye, RefreshCw } from 'lucide-vue-next';
import type { PlanCandidate, PlanProcessConstraint, Project } from '../../types';
import {
  candidateApplication,
  candidateNotice,
  candidateStatusLabel,
  contractRows,
  requirementSource,
} from '../../lib/planPreview';
import { useWorkspace } from '../../composables/useWorkspace';
import { useAgent } from '../../composables/useAgent';
import DiagramView from '../design/DiagramView.vue';
import PlanReviewDisclosure from './PlanReviewDisclosure.vue';
const props = defineProps<{ candidate: PlanCandidate; canonical: Project }>();
const { state, perform } = useWorkspace();
const agent = useAgent();
const project = computed(() => props.candidate.project);
const architecture = computed(() => project.value.architectures.at(-1));
const rows = computed(() => contractRows(project.value));
const requirements = computed(() => project.value.plan_contract?.requirements ?? []);
const processConstraints = computed(() => project.value.plan_contract?.process_constraints ?? []);
const checks = computed(() => {
  const review = props.candidate.report?.semantic;
  return candidateApplication(props.candidate) !== 'noop' &&
    review?.candidate_hash &&
    review.candidate_hash === props.candidate.candidate_hash
    ? review.checks
    : [];
});
const findings = computed(() =>
  (props.candidate.report?.findings ?? []).filter((finding) => finding.severity !== 'review'),
);
const advisories = computed(() =>
  (props.candidate.report?.findings ?? []).filter((finding) => finding.severity === 'review'),
);
function capabilityProvider(key: string) {
  const providers = rows.value.filter((row) =>
    row.binding.provides?.some((item) => item.key === key),
  );
  if (providers.length !== 1) return providers.length ? '重复提供者' : '未找到提供者';
  const row = providers[0]!;
  return `${row.owner?.title ?? '未找到所属步骤'} · ${row.binding.behavior_key}`;
}
const tab = ref('contracts');
const focusedId = ref('');
watch(
  () => props.candidate.id,
  () => {
    tab.value = 'contracts';
    focusedId.value = '';
  },
);
const focused = computed(() =>
  architecture.value?.diagram.nodes.find((node) => node.id === focusedId.value),
);
const active = computed(() =>
  ['generating', 'reviewing', 'ready'].includes(props.candidate.status),
);
const live = computed(
  () =>
    active.value &&
    agent.state.running &&
    agent.state.projectId === props.canonical.id &&
    agent.state.turnId === props.candidate.id,
);
const clock = ref(Date.now());
let clockTimer: ReturnType<typeof setInterval> | undefined;
watch(
  live,
  (running) => {
    clearInterval(clockTimer);
    clockTimer = undefined;
    clock.value = Date.now();
    if (running)
      clockTimer = setInterval(() => {
        clock.value = Date.now();
      }, 1000);
  },
  { immediate: true },
);
onUnmounted(() => clearInterval(clockTimer));
const elapsedSeconds = computed(() => {
  const recorded = props.candidate.metrics?.elapsed_seconds ?? 0;
  const start = Date.parse(props.candidate.created_at ?? '');
  return Math.round(
    live.value && Number.isFinite(start)
      ? Math.max(recorded, (clock.value - start) / 1000)
      : recorded,
  );
});
const requirementKind = { outcome: '结果', constraint: '约束', exclusion: '排除项' };
const processRule = {
  planning_only: '仅做规划',
  no_external_research: '不进行外部检索',
  other: '其他过程约束',
};
function processSource(constraint: PlanProcessConstraint) {
  return (
    project.value.plan_contract?.sources?.find((item) => item.id === constraint.source_id)?.text ??
    props.canonical.messages?.find((item) => item.id === constraint.source_id)?.content ??
    ''
  );
}
const verdict = { supported: '未发现问题', contradicted: '存在矛盾', unknown: '尚无法判断' };
const reviewBasis = {
  model_inference: '模型推断',
  source_statement: '模型引用原文',
  existing_execution_record: '引用既有验收记录',
};
const source = (requirement: Parameters<typeof requirementSource>[1]) =>
  requirementSource({ ...project.value, messages: props.canonical.messages }, requirement);
async function discard() {
  if (state.busy || ['applied', 'discarded'].includes(props.candidate.status)) return;
  await perform('plan.candidate_discard', {
    project_id: props.canonical.id,
    candidate_id: props.candidate.id,
  });
}
</script>
<template>
  <section class="plan-candidate-panel" aria-label="候选方案预览">
    <header class="candidate-heading">
      <div>
        <p class="candidate-eyebrow"><Eye :size="13" aria-hidden="true" />候选预览 · 只读</p>
        <h2>
          {{ candidateStatusLabel(candidate) }}
          <span>基于正式方案 R{{ candidate.base_revision }}</span>
        </h2>
        <p class="candidate-notice" role="status">
          <RefreshCw v-if="active && state.busy" :size="13" class="spinning" aria-hidden="true" />{{
            candidateNotice(candidate, canonical.revision)
          }}
        </p>
        <p v-if="candidate.generation_progress" class="candidate-eyebrow">
          {{
            candidate.generation_progress.checkpoint_count > 0
              ? `已保存 ${candidate.generation_progress.checkpoint_count} 段规划改动`
              : '尚未保存规划改动'
          }}
          ·
          {{ candidate.generation_progress.state === 'ready' ? '已提交统一检查' : '尚待完成生成' }}
        </p>
        <PlanReviewDisclosure :candidate="candidate" />
      </div>
      <button
        v-if="!['applied', 'discarded'].includes(candidate.status)"
        class="text-button"
        type="button"
        :disabled="state.busy"
        @click="discard"
      >
        放弃候选
      </button>
    </header>
    <div
      v-if="findings.length || checks.some((check) => check.verdict !== 'supported')"
      class="candidate-findings"
      role="status"
    >
      <strong><AlertCircle :size="14" aria-hidden="true" />待解决项</strong>
      <ul>
        <li v-for="(finding, index) in findings" :key="`${finding.code}:${index}`">
          <span>{{ finding.subject }} · {{ finding.code }}</span
          >{{ finding.message }}
        </li>
        <li
          v-for="(check, index) in checks.filter((item) => item.verdict !== 'supported')"
          :key="`semantic:${index}`"
        >
          <span>{{ check.subject }} · {{ verdict[check.verdict] }}</span
          >{{ check.reason }}
          <p v-if="check.counterexample">反例：{{ check.counterexample }}</p>
        </li>
      </ul>
    </div>
    <details v-if="advisories.length" class="candidate-review-details">
      <summary>非阻断建议 · {{ advisories.length }} 项</summary>
      <p v-for="(finding, index) in advisories" :key="`${finding.code}:${index}`">
        {{ finding.message }}
      </p>
    </details>
    <nav class="candidate-tabs" aria-label="候选内容">
      <button type="button" :aria-pressed="tab === 'contracts'" @click="tab = 'contracts'">
        需求与实现 <span>{{ requirements.length }}</span>
      </button>
      <button type="button" :aria-pressed="tab === 'milestones'" @click="tab = 'milestones'">
        交付步骤 <span>{{ project.milestones.length }}</span>
      </button>
      <button type="button" :aria-pressed="tab === 'architecture'" @click="tab = 'architecture'">
        架构预览 <span>{{ architecture?.diagram.nodes.length ?? 0 }}</span>
      </button>
    </nav>
    <div v-if="tab === 'contracts'" class="candidate-content">
      <section
        v-if="!requirements.length"
        class="candidate-pending-input"
        aria-label="当前请求原文，待整理"
      >
        <div class="candidate-item-meta"><strong>当前请求原文</strong><span>待整理</span></div>
        <p class="requirement-quote">{{ candidate.input }}</p>
        <p class="candidate-input-note">
          {{
            active
              ? '正在提取需求；生成的内容会逐步出现在这里'
              : '尚未整理为需求锚点，原始请求已保留'
          }}
        </p>
      </section>
      <section
        v-if="processConstraints.length"
        class="candidate-process-constraints"
        aria-label="本轮过程约束"
      >
        <h3 class="candidate-section-title">本轮过程约束</h3>
        <p class="candidate-process-note">
          按来源请求留存；每条仅约束所标注请求的规划过程，不计入产品需求或验收，也不表示永久产品承诺。
        </p>
        <article
          v-for="constraint in processConstraints"
          :key="constraint.id"
          class="candidate-process-constraint"
        >
          <div class="candidate-item-meta">
            <strong>{{ constraint.id }}</strong
            ><span>{{ processRule[constraint.rule] }}</span>
          </div>
          <p class="requirement-quote">{{ constraint.quote }}</p>
          <p class="candidate-process-scope">仅适用于来源请求：{{ constraint.source_id }}</p>
          <details class="requirement-source">
            <summary>来源请求原文 · {{ constraint.source_id }}</summary>
            <p v-if="processSource(constraint)">{{ processSource(constraint) }}</p>
            <p v-else>来源全文未包含在当前视图中；上方保留精确引文和来源请求 ID</p>
          </details>
        </article>
      </section>
      <section v-if="requirements.length" class="candidate-requirements" aria-label="原始需求">
        <article
          v-for="requirement in requirements"
          :id="`candidate-requirement-${requirement.id}`"
          :key="requirement.id"
          :class="{ retired: !requirement.active }"
        >
          <div class="candidate-item-meta">
            <strong>{{ requirement.id }}</strong
            ><span>{{ requirementKind[requirement.kind] }}</span
            ><span v-if="!requirement.active">已退役</span>
          </div>
          <p class="requirement-quote">{{ requirement.quote }}</p>
          <details class="requirement-source">
            <summary>{{ source(requirement).label }} · {{ requirement.source_id }}</summary>
            <p v-if="source(requirement).available">{{ source(requirement).content }}</p>
            <p v-else>来源记录未包含在当前视图中；上方保留精确引文和来源 ID</p>
          </details>
          <p v-if="requirement.retired_by" class="retirement-note">
            退役依据 · {{ requirement.retired_by.source_id }}：{{ requirement.retired_by.quote
            }}<br />{{ requirement.retired_by.reason }}
          </p>
        </article>
      </section>
      <h3 v-if="rows.length" class="candidate-section-title">验收与实现对应</h3>
      <article
        v-for="(row, index) in rows"
        :key="`${row.binding.behavior_revision_id}:${index}`"
        class="candidate-contract"
      >
        <div class="candidate-item-meta">
          <strong>{{ row.binding.behavior_key }}</strong
          ><span>{{ row.binding.behavior_revision_id }}</span>
        </div>
        <h3>{{ row.behavior?.statement || '未找到该版本的验收内容' }}</h3>
        <dl>
          <div>
            <dt>需求依据</dt>
            <dd>
              <a
                v-for="requirement in row.requirements"
                :key="requirement.id"
                :href="`#candidate-requirement-${requirement.id}`"
                >{{ requirement.id }}{{ requirement.requirement ? '' : '（未找到）' }}</a
              ><span v-if="!row.requirements.length">尚未关联需求</span>
            </dd>
          </div>
          <div>
            <dt>实现机制</dt>
            <dd>{{ row.binding.mechanism || '尚未说明' }}</dd>
          </div>
          <div>
            <dt>涉及组件</dt>
            <dd>
              <span
                v-for="component in row.components"
                :key="component.id"
                class="candidate-inline-item"
                >{{ component.component?.label || '未找到组件' }} · {{ component.id }}</span
              ><span v-if="!row.components.length">尚未关联组件</span>
            </dd>
          </div>
          <div>
            <dt>交付归属</dt>
            <dd>
              {{ row.owner?.title || '未找到所属步骤'
              }}<span v-if="row.behavior?.owner"> · {{ row.behavior.owner }}</span>
            </dd>
          </div>
          <div v-if="row.binding.requires_behavior_keys.length">
            <dt>前置验收</dt>
            <dd>{{ row.binding.requires_behavior_keys.join('、') }}</dd>
          </div>
        </dl>
        <details
          v-if="row.binding.provides?.length || row.binding.steps?.length"
          class="candidate-review-details"
        >
          <summary>
            动作声明 · 提供 {{ row.binding.provides?.length ?? 0 }} 项 · 验收
            {{ row.binding.steps?.length ?? 0 }} 步
          </summary>
          <p v-for="capability in row.binding.provides ?? []" :key="capability.key">
            {{ capability.kind === 'command' ? '操作' : '查询' }} {{ capability.key }}：{{
              capability.action
            }}
          </p>
          <p v-for="(step, stepIndex) in row.binding.steps ?? []" :key="stepIndex">
            <template v-if="step.kind === 'inspect'">检查范围 {{ step.requirement_id }}</template>
            <template v-else>
              {{ step.kind === 'invoke_command' ? '调用操作' : '调用查询' }}
              {{ step.capability_key }} · {{ capabilityProvider(step.capability_key) }}
            </template>
            ：{{ step.quote }}
          </p>
          <p class="candidate-caveat">
            这些是规划中的动作声明，程序检查其可用范围；未执行实现，也不证明动作提取完整
          </p>
        </details>
      </article>
      <p v-if="requirements.length && !rows.length" class="candidate-empty">
        需求与验收的对应关系尚未生成
      </p>
      <details v-if="checks.length" class="candidate-review-details">
        <summary>语义评审记录 · {{ checks.length }} 项</summary>
        <p>{{ candidate.report.semantic?.summary }}</p>
        <article v-for="(check, index) in checks" :key="index">
          <strong>{{ check.subject }} · {{ verdict[check.verdict] }}</strong>
          <p class="candidate-caveat">
            理由类型：{{ reviewBasis[check.basis ?? 'model_inference']
            }}<span v-if="check.execution_evidence_ids?.length">
              · 既有记录
              {{ check.execution_evidence_ids.join('、') }}（非本轮执行，未重新验证）</span
            >
          </p>
          <p>{{ check.reason }}</p>
          <p v-if="check.counterexample">反例：{{ check.counterexample }}</p>
        </article>
        <p class="candidate-caveat">这是模型对方案的评审意见，不代表代码已经实现或通过测试</p>
      </details>
    </div>
    <div v-else-if="tab === 'milestones'" class="candidate-content">
      <p v-if="!project.milestones.length" class="candidate-empty">交付步骤尚未生成</p>
      <article
        v-for="milestone in project.milestones"
        :key="milestone.id"
        class="candidate-contract"
      >
        <div class="candidate-item-meta">
          <strong>{{ milestone.id }}</strong
          ><span>规划步骤 · 未执行</span>
        </div>
        <h3>{{ milestone.title }}</h3>
        <p>{{ milestone.intent }}</p>
        <dl>
          <div>
            <dt>前置步骤</dt>
            <dd>{{ milestone.dependencies.join('、') || '无' }}</dd>
          </div>
          <div>
            <dt>涉及组件</dt>
            <dd>{{ milestone.architecture_components.join('、') || '尚未关联' }}</dd>
          </div>
        </dl>
        <ul class="candidate-acceptance">
          <li v-for="id in milestone.behavior_revision_ids" :key="id">
            {{
              project.behaviors.find((behavior) => behavior.id === id)?.statement ||
              `未找到验收内容 · ${id}`
            }}
          </li>
        </ul>
      </article>
    </div>
    <div v-else class="candidate-architecture">
      <template v-if="architecture">
        <details
          v-if="architecture.summary"
          :key="`${candidate.id}:architecture-summary`"
          class="candidate-architecture-summary"
        >
          <summary>架构说明 <span>展开查看全文</span></summary>
          <p tabindex="0" aria-label="完整架构说明">{{ architecture.summary }}</p>
        </details>
        <DiagramView
          :key="candidate.id"
          :diagram="architecture.diagram"
          :isolated="true"
          :focused-id="focusedId"
          @select="focusedId = focusedId === $event ? '' : $event"
        />
        <div v-if="focused" class="candidate-component-detail">
          <strong>{{ focused.label }} · {{ focused.id }}</strong>
          <p>{{ focused.description }}</p>
          <button class="text-button" type="button" @click="focusedId = ''">取消选择</button>
        </div>
      </template>
      <p v-else class="candidate-empty">架构尚未生成；已有需求和步骤可以先查看</p>
    </div>
    <footer class="candidate-footer">
      <span>{{ candidate.id }}</span
      ><span
        >请求尝试 {{ candidate.metrics?.provider_calls ?? 0 }} 次 ·
        {{
          candidate.metrics?.usage_reported
            ? `已报告 ${candidate.metrics.tokens} tokens`
            : 'token 用量未返回'
        }}
        · {{ live ? '已等待约 ' : '' }}{{ elapsedSeconds }} 秒</span
      >
    </footer>
  </section>
</template>
<style scoped>
.plan-candidate-panel {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  color: var(--ink);
  background: #f8faff;
}
.candidate-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 22px 15px;
  border-bottom: 1px solid #dbe4f1;
  background: #f1f5fc;
}
.candidate-eyebrow,
.candidate-notice,
.candidate-findings strong {
  display: flex;
  align-items: center;
  gap: 6px;
}
.candidate-eyebrow {
  margin: 0 0 7px;
  font-size: 11px;
  color: var(--accent);
}
.candidate-heading h2 {
  margin: 0;
  font-size: 17px;
}
.candidate-heading h2 span {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-secondary);
}
.candidate-notice {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.65;
}
.candidate-notice svg {
  flex-shrink: 0;
}
.candidate-heading > button {
  white-space: nowrap;
  font-size: 11px;
}
.candidate-findings {
  margin: 14px 22px 0;
  border: 1px solid #ead5b1;
  background: #fffaf1;
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
}
.candidate-findings ul {
  margin: 8px 0 0;
  padding-left: 19px;
}
.candidate-findings li + li {
  margin-top: 7px;
}
.candidate-findings li span {
  display: block;
  font-size: 10px;
  color: #89632a;
}
.candidate-findings p {
  margin: 3px 0 0;
}
.candidate-tabs {
  display: flex;
  gap: 6px;
  padding: 12px 22px;
  border-bottom: 1px solid var(--line);
}
.candidate-tabs button {
  padding: 7px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
}
.candidate-tabs button[aria-pressed='true'] {
  color: var(--accent);
  border-color: #d0dced;
  background: white;
}
.candidate-tabs span {
  margin-left: 5px;
  font-size: 10px;
}
.candidate-content {
  padding: 16px 22px;
}
.candidate-process-constraints {
  margin-bottom: 18px;
}
.candidate-process-note,
.candidate-process-scope {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.7;
  margin: 8px 0;
}
.candidate-process-constraint {
  border: 1px dashed #c9d6e8;
  border-radius: 8px;
  background: #f3f6fb;
  padding: 12px 15px;
  overflow-wrap: anywhere;
}
.candidate-process-constraint + .candidate-process-constraint {
  margin-top: 8px;
}
.candidate-requirements {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}
.candidate-requirements article,
.candidate-contract {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: white;
  padding: 13px 15px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.candidate-requirements .retired {
  background: #f4f5f7;
}
.candidate-item-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 10px;
  color: var(--text-secondary);
}
.candidate-item-meta strong {
  color: var(--accent);
}
.requirement-quote {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.7;
  margin: 9px 0;
}
.requirement-source {
  font-size: 10px;
  color: var(--text-secondary);
}
.requirement-source summary,
.candidate-review-details summary {
  cursor: pointer;
}
.requirement-source p {
  white-space: pre-wrap;
  line-height: 1.6;
}
.retirement-note {
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
}
.candidate-section-title {
  margin: 22px 0 10px;
  font-size: 13px;
}
.candidate-contract + .candidate-contract {
  margin-top: 10px;
}
.candidate-contract h3 {
  margin: 8px 0 10px;
  font-size: 14px;
  font-weight: 500;
}
.candidate-contract p,
.candidate-contract dl,
.candidate-acceptance {
  font-size: 12px;
  line-height: 1.7;
}
.candidate-contract dl {
  margin: 0;
}
.candidate-contract dl > div {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 9px;
  margin-top: 7px;
}
.candidate-contract dt {
  color: var(--text-secondary);
}
.candidate-contract dd {
  margin: 0;
  white-space: pre-wrap;
}
.candidate-contract a {
  color: var(--accent);
  margin-right: 10px;
}
.candidate-inline-item {
  display: inline-block;
  margin-right: 12px;
}
.candidate-pending-input {
  padding: 14px 16px;
  border: 1px dashed #c9d6e8;
  border-radius: 8px;
  background: white;
  overflow-wrap: anywhere;
}
.candidate-pending-input .requirement-quote {
  max-height: min(40vh, 260px);
  overflow: auto;
}
.candidate-input-note {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.7;
  margin: 8px 0 0;
}
.candidate-architecture-summary {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.7;
}
.candidate-architecture-summary summary {
  cursor: pointer;
  padding: 7px 0;
}
.candidate-architecture-summary summary span {
  margin-left: 8px;
  color: var(--text-secondary);
  font-size: 10px;
}
.candidate-architecture-summary p {
  max-height: min(25vh, 180px);
  overflow: auto;
  margin: 4px 0 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  overscroll-behavior: contain;
}
.candidate-empty {
  padding: 22px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.7;
}
.candidate-review-details {
  margin-top: 18px;
  padding: 12px 0;
  font-size: 12px;
  line-height: 1.7;
}
.candidate-review-details article {
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
.candidate-review-details p {
  margin: 5px 0;
}
.candidate-caveat {
  color: var(--text-secondary);
  font-size: 11px;
}
.candidate-architecture {
  padding: 14px 22px;
  flex: 1;
}
.candidate-architecture > p {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.7;
}
.candidate-architecture :deep(.design-diagram) {
  height: 420px;
  min-height: 300px;
  border: 1px solid var(--line);
  border-radius: 8px;
}
.candidate-component-detail {
  border: 1px solid var(--line);
  padding: 12px;
  margin-top: 10px;
  border-radius: 6px;
  background: white;
  font-size: 12px;
  line-height: 1.6;
}
.candidate-footer {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: auto;
  padding: 12px 22px;
  color: var(--text-secondary);
  font-size: 10px;
  border-top: 1px solid var(--line);
}
@media (max-width: 760px) {
  .candidate-heading {
    padding: 14px 12px;
  }
  .candidate-content,
  .candidate-architecture {
    padding: 12px;
  }
  .candidate-findings {
    margin-inline: 12px;
  }
  .candidate-tabs {
    padding-inline: 12px;
  }
  .candidate-heading h2 span {
    display: block;
    margin: 5px 0 0;
  }
  .candidate-requirements {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
