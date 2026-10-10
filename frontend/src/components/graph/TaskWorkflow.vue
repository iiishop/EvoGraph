<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue';
import type { Project, Milestone } from '../../types';
import { command } from '../../api/client';
import { useWorkspace } from '../../composables/useWorkspace';
import { useAgent } from '../../composables/useAgent';
import {
  workflowDrafts,
  runWorkflowAction,
  savedPromptFreshness,
} from '../../composables/useWorkflowDrafts';
import { copyText } from '../../lib/copyText';
import ObligationItem from './ObligationItem.vue';
const props = defineProps<{ project: Project; milestone: Milestone }>();
const { state, refresh, setBusy, selectNode } = useWorkspace();
const agent = useAgent();
const { draft, report } = workflowDrafts.bind(
  () => props.project.id,
  () => props.milestone.id,
);
let mounted = true;
let viewGeneration = 0;
watch(
  [
    () => props.project.id,
    () => props.milestone.id,
    () => state.project?.id,
    () => state.selectedId,
  ],
  () => {
    viewGeneration++;
  },
  { flush: 'sync' },
);
onBeforeUnmount(() => {
  mounted = false;
  viewGeneration++;
});
const ready = computed(
  () => props.project.readiness[props.milestone.id] ?? { blockers: [], safe_to_execute: false },
);
const blocked = computed(
  () => state.busy || Boolean(draft.value?.pending || draft.value?.syncError),
);
const promptFreshness = computed(() => savedPromptFreshness(draft.value, props.project.revision));
const promptLabel = computed(() => {
  const kind =
    draft.value?.promptKind === 'implementation'
      ? '制作提示词'
      : draft.value?.promptKind === 'verification'
        ? '验收提示词'
        : '提示词（类型未知）';
  return `${promptFreshness.value === 'older_revision' ? '历史' : promptFreshness.value === 'unknown' ? '未核对的' : '已保存的'}${kind}`;
});
const basisChanged = computed(() =>
  Boolean(
    props.milestone.pinned_baseline &&
    props.milestone.pinned_baseline !== props.project.baselines.at(-1)?.id,
  ),
);
const needsRevalidation = computed(
  () => props.milestone.status === 'REVALIDATION_REQUIRED' || basisChanged.value,
);
const stage = computed(() =>
  props.milestone.status === 'VERIFIED_COMPLETE'
    ? 3
    : props.milestone.status === 'AWAITING_ACCEPTANCE'
      ? 2
      : props.milestone.lease_active
        ? 1
        : 0,
);
async function run(action: string) {
  if (blocked.value) return;
  const attempt = workflowDrafts.start(props.project.id, props.milestone.id);
  if (!attempt) return;
  const title = props.milestone.title;
  const generation = viewGeneration;
  const origin = () =>
    mounted &&
    generation === viewGeneration &&
    props.project.id === attempt.projectId &&
    props.milestone.id === attempt.milestoneId &&
    state.project?.id === attempt.projectId &&
    state.selectedId === attempt.milestoneId &&
    workflowDrafts.entry(attempt.projectId, attempt.milestoneId) === attempt.entry;
  const params: Record<string, unknown> = {
    project_id: attempt.projectId,
    ...(action === 'baseline.refresh' ? {} : { milestone_id: attempt.milestoneId }),
  };
  if (action === 'verification.import') {
    try {
      params.report = JSON.parse(
        attempt.report
          .trim()
          .replace(/^```(?:json)?\s*/, '')
          .replace(/\s*```$/, ''),
      );
    } catch {
      attempt.entry.error = '报告不是有效 JSON，请粘贴外部 Agent 返回的完整报告。';
      attempt.entry.pending = null;
      return;
    }
  }
  setBusy(true);
  try {
    const outcome = await runWorkflowAction({
      store: workflowDrafts,
      attempt,
      mutate: () =>
        command<{ prompt?: string; project_revision?: number; result?: string }>(action, params),
      refresh,
      confirmed: (result, entry) => {
        if (result.prompt) {
          entry.prompt = result.prompt;
          entry.promptKind = action === 'verification.export' ? 'verification' : 'implementation';
          // The export owns this provenance; a later refresh may already be a newer version.
          entry.promptProjectRevision =
            Number.isSafeInteger(result.project_revision) && result.project_revision! >= 0
              ? result.project_revision!
              : null;
          entry.notice =
            action === 'verification.export'
              ? `「${title}」验收请求已生成`
              : `「${title}」制作提示词已生成`;
        } else if (action === 'verification.import') {
          entry.notice =
            result.result === 'PASS'
              ? `「${title}」验收通过，任务已释放`
              : `「${title}」验收未通过，保留任务继续制作`;
          // A confirmed older submission cannot erase edits made while it was in flight.
          if (entry.revision === attempt.revision) {
            entry.report = '';
            entry.revision++;
          }
          entry.prompt = '';
          entry.promptKind = null;
          entry.promptProjectRevision = null;
        } else {
          entry.notice =
            action === 'milestone.start'
              ? `「${title}」任务已领取，资源已锁定`
              : action === 'milestone.release'
                ? `「${title}」任务已释放，未标记为完成`
                : '基线与前置条件已重新检查';
        }
      },
    });
    // Never start a delayed clipboard write after leaving the originating inspector.
    if (
      outcome.confirmed &&
      outcome.current &&
      outcome.result.prompt &&
      origin() &&
      savedPromptFreshness(attempt.entry, state.project?.revision) === 'same_revision'
    ) {
      attempt.entry.copied = copyText(outcome.result.prompt);
      if (!attempt.entry.copied)
        attempt.entry.error = '自动复制未成功，请使用下方复制按钮，或选择提示词后手动复制。';
    }
  } finally {
    setBusy(false);
  }
}
function copySavedPrompt() {
  const entry = draft.value;
  if (blocked.value || !entry?.prompt) return;
  entry.copied = copyText(entry.prompt);
  entry.error = entry.copied ? '' : '剪贴板不可用，请从下方选择并复制提示词。';
}
async function sync() {
  if (state.busy || !draft.value) return;
  const entry = draft.value;
  const projectId = props.project.id,
    milestoneId = props.milestone.id;
  setBusy(true);
  try {
    await refresh();
    if (state.project?.id === projectId && workflowDrafts.entry(projectId, milestoneId) === entry)
      entry.syncError = '';
  } catch {
    /* Keep the confirmed-result warning and the report intact. */
  } finally {
    setBusy(false);
  }
}
function investigate() {
  agent.send(
    props.project.id,
    `调查里程碑 ${props.milestone.id} 的前置条件。读取源码并使用 resolve_investigation 记录真实依据；只对无法自行解决的信息或关键决策提问。`,
  );
}
</script>
<template>
  <div class="task-workflow">
    <ol class="workflow-steps" aria-label="任务流程">
      <li
        v-for="(label, i) in ['条件', needsRevalidation ? '需复核' : '制作', '验收', '完成']"
        :key="label"
        :class="{ active: i === stage, done: i < stage }"
        :aria-current="i === stage ? 'step' : undefined"
      >
        <span>{{ i < stage ? '✓' : i + 1 }}</span
        >{{ label }}
      </li>
    </ol>
    <section
      v-if="stage > 0 && (needsRevalidation || ready.blockers.length)"
      class="workflow-basis"
      aria-label="当前交付依据提醒"
    >
      <strong>{{
        needsRevalidation ? '基线已变化，需重新核对依据' : '当前前置条件仍有待处理项'
      }}</strong>
      <p>已领取的任务和草稿保留；以下状态基于最近检查的仓库基线。</p>
      <ul v-if="ready.blockers.length">
        <li v-for="item in ready.blockers" :key="item">{{ item }}</li>
      </ul>
      <button class="button secondary" :disabled="blocked" @click="run('baseline.refresh')">
        重新检查基线
      </button>
    </section>
    <section v-if="stage === 0" class="task-next">
      <h3>先确认任务可以开始</h3>
      <p>依赖、调查依据与资源检查通过后，领取任务。</p>
      <ul v-if="ready.blockers.length" class="blockers">
        <li v-for="item in ready.blockers" :key="item">{{ item }}</li>
      </ul>
      <p v-else-if="ready.safe_to_execute" class="positive">前置条件已满足，可以领取。</p>
      <p v-else class="muted">尚未确认全部前置条件，请先检查最新状态。</p>
      <button class="button secondary" :disabled="blocked" @click="run('baseline.refresh')">
        检查前置条件</button
      ><button
        class="button primary"
        :disabled="blocked || !ready.safe_to_execute"
        @click="run('milestone.start')"
      >
        领取任务
      </button>
    </section>
    <section v-else-if="stage === 1" class="task-next">
      <h3>{{ needsRevalidation ? '核对后继续制作与验收' : '交给外部 Agent 制作' }}</h3>
      <p>复制任务范围与设计约束。制作完成后，生成绑定最新基线的验收提示词。</p>
      <button class="button secondary" :disabled="blocked" @click="run('implementation.export')">
        复制制作提示词</button
      ><button class="button primary" :disabled="blocked" @click="run('verification.export')">
        制作完成，复制验收提示词
      </button>
    </section>
    <section v-else-if="stage === 2" class="task-next">
      <h3>等待外部验收报告</h3>
      <p>将提示词交给外部 Agent，粘贴它返回的 JSON。全部行为通过后自动完成并释放任务。</p>
      <button class="button secondary" :disabled="blocked" @click="run('verification.export')">
        重新生成验收提示词</button
      ><small>重新生成会使之前的验收请求失效。</small
      ><label class="report-label" for="acceptance-report">验收报告</label
      ><textarea
        id="acceptance-report"
        v-model="report"
        rows="7"
        spellcheck="false"
        placeholder="粘贴完整 JSON 报告"
      /><button
        class="button primary"
        :disabled="blocked || !report.trim()"
        @click="run('verification.import')"
      >
        导入报告并更新状态</button
      ><small>这是外部报告的结论；EvoGraph 不在本地运行验收。</small>
    </section>
    <section v-else class="task-next completed-receipt">
      <h3>验收通过，任务已释放</h3>
      <p>外部报告已归档，可在「记录」中查看。基线变化后会重新判断证据有效性。</p>
    </section>
    <details v-if="stage !== 2 && report" open class="retained-report-draft">
      <summary>未提交的报告草稿</summary>
      <p class="muted">这份较新的草稿未随上一份报告提交，已保留完整内容。</p>
      <label class="report-label" for="retained-report">保留的报告草稿</label>
      <textarea id="retained-report" v-model="report" rows="7" spellcheck="false" />
    </details>
    <p v-if="draft?.notice" class="workflow-notice" role="status">{{ draft.notice }}</p>
    <p v-if="draft?.error" class="inline-error" role="alert">{{ draft.error }}</p>
    <div v-if="draft?.syncError" class="workflow-sync" role="status">
      <p>{{ draft.syncError }}</p>
      <button class="button secondary" :disabled="state.busy" @click="sync">刷新状态</button>
    </div>
    <details v-if="draft?.prompt" open class="handoff-prompt">
      <summary>{{ promptLabel }}{{ draft?.copied ? ' · 已复制' : '' }}</summary>
      <p v-if="promptFreshness === 'older_revision'" class="workflow-basis" role="status">
        生成后项目已更新（生成版本 {{ draft.promptProjectRevision }}，当前显示版本
        {{ project.revision }}）。此处保留原文；再次交接前请核对，或使用上方按钮明确重新生成。
      </p>
      <p v-else-if="promptFreshness === 'unknown'" class="muted" role="status">
        生成版本未记录，或当前项目状态尚未同步，无法核对这份提示词。使用前请重新核对。
      </p>
      <p v-else class="muted">
        生成版本 {{ draft.promptProjectRevision }} 与当前显示的项目版本一致。
      </p>
      <small>这里只对照已显示的项目版本，不代表已检查仓库现状或内容正确。</small>
      <button class="button secondary" :disabled="blocked" @click="copySavedPrompt">
        复制{{ promptLabel }}
      </button>
      <textarea
        :value="draft?.prompt"
        readonly
        rows="7"
        :aria-label="promptLabel"
        @focus="($event.target as HTMLTextAreaElement).select()"
      />
    </details>
    <details class="task-conditions" :open="stage === 0">
      <summary>前置条件与调查依据</summary>
      <div v-for="id in milestone.dependencies" :key="id" class="dependency-item">
        <button @click="selectNode(id)">{{ id }}</button
        ><small>{{ milestone.dependency_reasons[id] }}</small>
      </div>
      <p class="muted">资源：{{ milestone.resources.join('、') || '未声明独立资源' }}</p>
      <button
        v-if="milestone.obligations.some((o) => !o.resolved)"
        class="button secondary"
        :disabled="blocked || !!project.question"
        @click="investigate"
      >
        让 Agent 调查</button
      ><ObligationItem
        v-for="item in milestone.obligations"
        :key="milestone.id + item.id"
        :obligation="item"
        :project-id="project.id"
        :milestone-id="milestone.id"
      />
    </details>
    <button
      v-if="milestone.lease_active"
      class="text-button release-task"
      :disabled="blocked"
      @click="run('milestone.release')"
    >
      暂时释放任务
    </button>
  </div>
</template>
<style scoped>
.workflow-basis {
  margin: 12px 0;
  padding: 13px;
  border: 1px solid #ead9b4;
  border-radius: 9px;
  background: #fff9eb;
  font-size: 12px;
  line-height: 1.6;
}
.workflow-basis p {
  margin: 7px 0;
}
.workflow-basis ul {
  margin: 8px 0;
  padding-left: 18px;
}
</style>
