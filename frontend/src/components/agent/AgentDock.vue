<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import PlanningJobPanel from './PlanningJobPanel.vue';
import AgentQuestion from './AgentQuestion.vue';
import AgentReviewTray from './AgentReviewTray.vue';
import { latestTurnSummary, parseTurnSummary, turnSummaryHeadline } from '../../lib/turnSummary';
import { planAgentRetry, waitForRetryRead } from '../../lib/agentRetry';
import FollowAgentButton from '../graph/FollowAgentButton.vue';
import AttachmentPicker from '../attachments/AttachmentPicker.vue';
import AttachmentReceipt from '../attachments/AttachmentReceipt.vue';
import ComposerEditor from './ComposerEditor.vue';
import {
  cloneComposerDocument,
  trimComposerDocument,
  renderComposerDocument,
  textDocument,
  documentAttachmentIds,
  composerDocumentIssue,
} from '../../lib/composerDocument';
import { ArrowUp, Square, ChevronDown, ChevronUp } from 'lucide-vue-next';
import { command } from '../../api/client';
import { useAgent } from '../../composables/useAgent';
import {
  agentDrafts,
  useAgentDraft,
  type DraftAttempt,
  type FailedDraft,
} from '../../composables/useAgentDrafts';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project, ReferenceItem, PlanningJobLimits, PlanningJobRequest } from '../../types';

const props = defineProps<{
  project: Project;
  compact?: boolean;
  view?: string;
  reviewOwner?: number;
}>();
defineEmits<{ resume: []; locate: [id: string] }>();
const dockCollapsed = ref(false);
// Expanded planning is never selected implicitly or carried to another project.
const boundedJob = ref(false);
const completeChange = ref(false);
const jobConfirmed = ref(false);
const jobLimits = ref<PlanningJobLimits>({ max_phases: 1, max_calls: 5, max_input_bytes: 393216 });
const effectiveJobLimits = computed<PlanningJobLimits>(() =>
  completeChange.value ? { max_phases: 1, max_calls: 2, max_input_bytes: 393216 } : jobLimits.value,
);
const jobSupported = computed(
  () =>
    Boolean(props.project.unified_planning && props.project.planning_job_start_pins) &&
    !props.project.question,
);
const jobLimitsValid = computed(
  () =>
    completeChange.value ||
    (Object.values(jobLimits.value).every((value) => Number.isSafeInteger(value) && value > 0) &&
      jobLimits.value.max_phases <= 3 &&
      jobLimits.value.max_calls <= 12 &&
      jobLimits.value.max_calls <= 5 * jobLimits.value.max_phases &&
      jobLimits.value.max_input_bytes <= 1179648 &&
      jobLimits.value.max_input_bytes <= 393216 * jobLimits.value.max_phases),
);
watch(
  () => [props.project.id, props.project.created_at],
  () => {
    boundedJob.value = false;
    completeChange.value = false;
    jobConfirmed.value = false;
    jobLimits.value = { max_phases: 1, max_calls: 5, max_input_bytes: 393216 };
  },
  { flush: 'sync' },
);
watch(
  () => [
    boundedJob.value,
    completeChange.value,
    JSON.stringify(jobLimits.value),
    JSON.stringify(props.project.planning_job_start_pins),
  ],
  () => {
    jobConfirmed.value = false;
  },
  { flush: 'sync' },
);
watch(
  boundedJob,
  (enabled) => {
    if (!enabled) completeChange.value = false;
  },
  { flush: 'sync' },
);
const review = ref<HTMLElement>();
watch(
  () => props.project.id,
  () => {
    if (review.value) review.value.scrollTop = 0;
  },
);
const turnSummary = computed(() => latestTurnSummary(props.project));
const agent = useAgent();
const { state, setPage, setError, selectProject, applyProject } = useWorkspace();
const {
  content,
  composerDocument,
  attachmentIds,
  failures,
  pending,
  failureRestored,
  restoredFailure,
  recoveryError,
  attachmentTransfer,
} = useAgentDraft(() => props.project.id);
// New submissions append their recovery snapshot. Keep the visible retry and
// its receipt on the newest request; older snapshots remain recoverable.
const failedAttempt = computed(() => failures.value.at(-1));
const failedCompleteChange = computed(
  () =>
    failedAttempt.value?.planningJob?.action === 'start' &&
    failedAttempt.value.planningJob.mode === 'bounded-complete-change/v1',
);
const failedRequestSnippet = computed(() => {
  const text = failedAttempt.value?.text.replace(/\s+/g, ' ').trim() ?? '';
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
});
const failedCandidateReceipt = computed(() => {
  const turnId = failedAttempt.value?.turnId;
  if (!turnId) return null;
  return (
    (props.project.events ?? [])
      .filter((event) => event.kind === 'agent_turn_finished')
      .map((event) => parseTurnSummary(event.detail))
      .find((summary) => summary?.turn_id === turnId && summary.candidate_outcome) ?? null
  );
});
const recoveryNotice = computed(() => {
  if (failedCandidateReceipt.value)
    return `${failureRestored.value ? '请求内容已放回输入框' : '未完成请求已保留，当前草稿未改动'}；${turnSummaryHeadline(failedCandidateReceipt.value)}`;
  return failureRestored.value
    ? '本轮未完成，内容已放回输入框；已保存的修改会保留。'
    : '未完成请求已保留，当前草稿未改动；已保存的修改会保留。';
});
const failedQuestion = computed(() => {
  const prompt = failedAttempt.value?.question?.prompt ?? '';
  return prompt.length > 240 ? `${prompt.slice(0, 240)}…` : prompt;
});
let mounted = true;
onUnmounted(() => {
  mounted = false;
});
const message = ref<InstanceType<typeof ComposerEditor>>();
const reviewTray = ref<InstanceType<typeof AgentReviewTray>>();
// Entry hints focus the existing editor; they never populate or send a draft.
defineExpose({
  openReceipt: async (eventId: string, trigger: HTMLElement) => {
    const projectId = props.project.id;
    const incarnation = props.project.created_at;
    const owner = props.reviewOwner;
    dockCollapsed.value = false;
    await nextTick();
    if (
      mounted &&
      props.project.id === projectId &&
      props.project.created_at === incarnation &&
      props.reviewOwner === owner
    )
      await reviewTray.value?.openReceipt(eventId, trigger);
  },
  focus: async () => {
    dockCollapsed.value = false;
    await nextTick();
    if (mounted) message.value?.focus();
  },
});
const attachmentPicker = ref<InstanceType<typeof AttachmentPicker>>();
const dragDepth = ref(0);
const references = ref<ReferenceItem[] | null>(null);
const referenceError = ref('');
const referenceWarnings = ref<string[]>([]);
const editorError = ref('');
let referenceSequence = 0;
let loadedCatalogKey = '';
let pendingCatalogKey = '';
const catalogKey = () => `${props.project.id}:${props.project.revision ?? 0}`;
const editorDocument = computed({
  get: () => composerDocument.value ?? textDocument(content.value),
  set: (value) => {
    composerDocument.value = value;
  },
});
const inlineAttachmentIds = computed(() => documentAttachmentIds(composerDocument.value));
const referencedAttachmentIds = computed(() => [
  ...new Set([...attachmentIds.value, ...inlineAttachmentIds.value]),
]);
const documentIssue = computed(() =>
  composerDocument.value
    ? composerDocumentIssue(
        composerDocument.value,
        props.project.id,
        references.value,
        attachmentIds.value,
      )
    : null,
);
watch(
  () => [props.project.id, props.project.revision],
  () => {
    referenceSequence++;
    references.value = null;
    loadedCatalogKey = '';
    pendingCatalogKey = '';
    referenceError.value = '';
    referenceWarnings.value = [];
    editorError.value = '';
    message.value?.closeSuggestions();
    if (composerDocument.value?.parts.some((part) => part.type === 'reference'))
      void loadReferences();
  },
  { immediate: true },
);
watch(composerDocument, (document) => {
  if (
    document?.parts.some((part) => part.type === 'reference') &&
    loadedCatalogKey !== catalogKey()
  )
    void loadReferences();
});

const runningHere = computed(
  () => agent.state.running && agent.state.projectId === props.project.id,
);
const runningElsewhere = computed(
  () => agent.state.running && agent.state.projectId !== props.project.id,
);
const answering = computed(() => Boolean(props.project.question));
const requiresReview = computed(() => {
  const transfer = attachmentTransfer.value;
  return Boolean(
    answering.value ||
    failedAttempt.value ||
    (transfer &&
      (transfer.phase !== 'done' ||
        transfer.report?.failure ||
        transfer.report?.selectionError ||
        transfer.report?.refresh === 'failed' ||
        transfer.report?.refresh === 'timeout')),
  );
});
watch(
  () => props.compact,
  (compact) => {
    dockCollapsed.value = Boolean(
      compact && !content.value.trim() && !requiresReview.value && !runningHere.value,
    );
  },
  { immediate: true },
);
watch(
  () => props.project.question?.id,
  (id) => {
    if (id) dockCollapsed.value = false;
  },
);
watch(dragDepth, (depth) => {
  if (depth > 0) dockCollapsed.value = false;
});
const showStatus = computed(
  () =>
    runningElsewhere.value ||
    (agent.state.projectId === props.project.id && Boolean(agent.state.label)),
);
const hasReview = computed(() =>
  Boolean(
    props.project.messages.length ||
    (turnSummary.value && !runningHere.value) ||
    attachmentTransfer.value ||
    failedAttempt.value,
  ),
);
watch(
  () => [props.project.question?.id, failedAttempt.value?.id, attachmentTransfer.value?.id],
  ([question, failure, transfer]) => {
    if (question || failure || (transfer && requiresReview.value)) dockCollapsed.value = false;
  },
  { immediate: true },
);

// Why the send button is dead, spelled out instead of silently ignored.
const operationBlocker = computed(() => {
  if (!state.settings?.provider) return { text: '未配置模型，无法发送', action: '配置 Provider' };
  if (runningElsewhere.value) return { text: 'Agent 正在其他项目运行', action: '查看' };
  // 运行中时不再给提示行挂"停止"：右侧停止按钮就在同一行，重复一个操作
  if (runningHere.value) return { text: '上一轮仍在处理，可随时停止', action: '' };
  const transfer = attachmentTransfer.value;
  if (transfer && transfer.phase !== 'done') {
    const text =
      transfer.phase === 'preparing'
        ? '正在读取资料格式，暂不能发送'
        : transfer.phase === 'refreshing'
          ? '正在同步资料结果，完成后可发送'
          : '资料正在保存，确认后可发送';
    return { text, action: '' };
  }
  if (state.busy) return { text: '正在处理上一步操作', action: '' };
  if (pending.value) return { text: '正在确认上一条请求的结果', action: '' };
  return null;
});
const blocker = computed(
  () =>
    operationBlocker.value ||
    (documentIssue.value
      ? {
          text: referenceError.value || documentIssue.value,
          action: references.value === null ? '重试引用' : '',
        }
      : null),
);
const jobBlocker = computed(() =>
  !boundedJob.value
    ? ''
    : !jobSupported.value
      ? '当前状态不支持启动有界作业，请刷新或先回答待确认问题'
      : referencedAttachmentIds.value.length ||
          composerDocument.value?.parts.some((part) => part.type === 'reference')
        ? '有界作业目前只接收纯文字；请移除资料和引用，或关闭有界模式以保留原发送方式'
        : ['authorized', 'running', 'paused'].includes(props.project.planning_job?.status ?? '')
          ? '已有未结束作业，请先继续或取消该作业，再授权新作业'
          : !jobLimitsValid.value
            ? '额度须为正整数，最多 3 阶段、12 次调用和 1,179,648 B 累计输入；总调用不得超过阶段数 × 5，总输入不得超过阶段数 × 393,216 B'
            : !jobConfirmed.value
              ? '请勾选确认本次作业的总额度'
              : '',
);
const canSend = computed(
  () => Boolean(content.value.trim()) && !blocker.value && !jobBlocker.value,
);
watch(
  [content, composerDocument, attachmentIds],
  () => {
    jobConfirmed.value = false;
  },
  { deep: true, flush: 'sync' },
);
async function openJobAuthorization() {
  dockCollapsed.value = false;
  boundedJob.value = true;
  completeChange.value = props.project.planning_job?.mode === 'bounded-complete-change/v1';
  jobConfirmed.value = false;
  jobLimits.value = { max_phases: 1, max_calls: 5, max_input_bytes: 393216 };
  await nextTick();
  if (mounted) message.value?.focus();
}

const placeholder = computed(() =>
  answering.value
    ? '也可以在这里自己写回答，或直接点上方选项…'
    : '描述想法、补充约束，或说明希望调整的地方…',
);
const counter = computed(() => content.value.length);

function runBlockerAction() {
  if (!state.settings?.provider) return setPage('settings');
  if (runningElsewhere.value) return selectProject(agent.state.projectId);
  if (documentIssue.value && references.value === null) return loadReferences();
}

async function deliver(attempt: DraftAttempt) {
  let delivered = false;
  try {
    const document = attempt.request ? attempt.request.composerDocument : attempt.composerDocument;
    const outgoingDocument = document ? trimComposerDocument(document) : undefined;
    delivered = await agent.send(
      attempt.projectId,
      outgoingDocument
        ? renderComposerDocument(outgoingDocument)
        : (attempt.request?.text ?? attempt.text.trim()),
      attempt.request ? attempt.request.questionId : attempt.questionId,
      attempt.ids,
      attempt.request ? attempt.request.verificationMilestone : attempt.verificationMilestone,
      attempt.planningJob ? undefined : outgoingDocument,
      attempt,
      attempt.request ? attempt.request.sourceAnalysis : attempt.sourceAnalysis,
      undefined,
      attempt.planningJob,
    );
  } catch (error) {
    setError(error instanceof Error ? error.message : '请求未完成');
  }
  const restored = agentDrafts.settle(attempt, delivered);
  if (restored && mounted && props.project.id === attempt.projectId) {
    await nextTick();
    if (mounted && props.project.id === attempt.projectId) message.value?.focus();
  }
}

async function submit(
  text = content.value,
  questionId = props.project.question?.id,
  chosenOption = false,
) {
  message.value?.closeSuggestions();
  // Keep the exact draft for recovery; trim only the submitted payload.
  if (!text.trim() || (chosenOption ? operationBlocker.value : blocker.value)) return;
  if (!chosenOption && boundedJob.value && jobBlocker.value) return;
  const planningJob: PlanningJobRequest | undefined =
    !chosenOption && boundedJob.value && props.project.planning_job_start_pins
      ? {
          action: 'start',
          job_id: crypto.randomUUID(),
          ...(completeChange.value ? { mode: 'bounded-complete-change/v1' as const } : {}),
          limits: { ...effectiveJobLimits.value },
          pins: JSON.parse(JSON.stringify(props.project.planning_job_start_pins)),
        }
      : undefined;
  const restored = restoredFailure.value;
  if (!planningJob && !chosenOption && restored && text === content.value) {
    // Sending the untouched restored answer is also a retry, not permission to
    // silently bind it to a different question that arrived in the meantime.
    agentDrafts.recoverComposer(props.project.id);
    await retry(restored, true);
    return;
  }
  if (chosenOption && restored && restored.questionId !== questionId) {
    agentDrafts.recoverComposer(props.project.id);
    agentDrafts.detachRestored(props.project.id, restored.id);
  }
  const question = props.project.question;
  const attempt = agentDrafts.start(
    props.project.id,
    {
      text,
      ...(planningJob ? { planningJob } : {}),
      composerDocument: chosenOption
        ? textDocument(text)
        : cloneComposerDocument(composerDocument.value),
      ids: attachmentIds.value,
      questionId,
      question:
        question && question.id === questionId
          ? {
              id: question.id,
              prompt: question.prompt,
              context: question.context,
              verification_milestone: question.verification_milestone,
            }
          : undefined,
    },
    text === content.value || content.value === '',
  );
  if (attempt) {
    boundedJob.value = false;
    completeChange.value = false;
    jobConfirmed.value = false;
    jobLimits.value = { max_phases: 1, max_calls: 5, max_input_bytes: 393216 };
    await deliver(attempt);
  }
}

async function retry(failure: FailedDraft | undefined = failedAttempt.value, fromComposer = false) {
  // Saved recovery is independent of a newer composer draft. Its own document
  // is validated under the backend admission lock before anything is consumed.
  if (!failure || operationBlocker.value || (fromComposer && documentIssue.value)) return;
  const projectId = props.project.id;
  const preparation = agentDrafts.prepareRetry(projectId, failure.id, fromComposer);
  if (!preparation) return;
  const report = (message: string) => {
    agentDrafts.setRecoveryError(projectId, message);
    if (state.project?.id === projectId) setError(message);
  };
  try {
    const current = await waitForRetryRead(
      command<Project>('projects.get', { project_id: projectId }),
    );
    if (!agentDrafts.retryIsCurrent(preparation)) return;
    if (current.id !== projectId) throw new Error('项目状态不匹配，原请求已保留，请重新打开项目');
    if (agent.state.running || state.busy) {
      report('其他操作正在进行，原请求已保留，请稍后重试');
      return;
    }
    if (state.project?.id === projectId && state.project.revision > current.revision) {
      report('项目在读取期间已更新，原请求已保留，请重新重试以使用最新状态');
      return;
    }
    applyProject(current);
    const plan = planAgentRetry(preparation.failure, current);
    if (plan.kind === 'blocked') {
      if (plan.differentQuestion && !preparation.failure.composerDocument)
        agentDrafts.detachRestored(projectId, failure.id);
      report(plan.message);
      return;
    }
    const attempt = agentDrafts.commitRetry(preparation, plan.request);
    if (attempt) await deliver(attempt);
  } catch (error) {
    if (agentDrafts.retryIsCurrent(preparation))
      report(error instanceof Error ? error.message : '无法读取当前项目，原请求已保留');
  } finally {
    agentDrafts.cancelRetry(preparation);
  }
}

async function loadReferences() {
  const key = catalogKey();
  if (loadedCatalogKey === key || pendingCatalogKey === key) return;
  const sequence = ++referenceSequence;
  const projectId = props.project.id;
  pendingCatalogKey = key;
  referenceError.value = '';
  try {
    const result = await command<{ items: ReferenceItem[]; warnings?: string[] }>(
      'references.catalog',
      { project_id: projectId },
    );
    if (!mounted || sequence !== referenceSequence || catalogKey() !== key) return;
    references.value = result.items;
    referenceWarnings.value = result.warnings ?? [];
    loadedCatalogKey = key;
  } catch {
    if (mounted && sequence === referenceSequence && catalogKey() === key) {
      references.value = null;
      referenceError.value = '引用列表暂时不可用，草稿仍然保留。';
    }
  } finally {
    if (sequence === referenceSequence) pendingCatalogKey = '';
  }
}

function clipboardFiles(event: ClipboardEvent) {
  const data = event.clipboardData;
  if (!data) return [];
  const files = Array.from(data.files);
  if (files.length) return files;
  return Array.from(data.items)
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
}

function onPaste(event: ClipboardEvent) {
  const files = clipboardFiles(event);
  // Plain text keeps the browser's native paste behavior. File/image clipboard
  // entries are uploaded through the same picker path as drag-and-drop.
  if (!files?.length) return;
  event.preventDefault();
  void attachmentPicker.value?.acceptFiles(files);
}

const draggingFiles = computed(() => dragDepth.value > 0);

function onDragEnter(event: DragEvent) {
  if (event.dataTransfer && Array.from(event.dataTransfer.types).includes('Files')) {
    dragDepth.value += 1;
  }
}

function onDragLeave() {
  dragDepth.value = Math.max(0, dragDepth.value - 1);
}

function onDrop(event: DragEvent) {
  dragDepth.value = 0;
  if (event.dataTransfer?.files.length) {
    void attachmentPicker.value?.acceptFiles(event.dataTransfer.files);
  }
}

function choose(option: string) {
  // 选项本身就是一条完整回答，此时输入框通常是空的：不能用 canSend（它要求输入框非空）
  if (operationBlocker.value) return;
  void submit(option, props.project.question?.id, true);
}
</script>
<template>
  <section
    class="agent-dock"
    :class="{
      'is-drop-target': draggingFiles,
      'is-collapsed': dockCollapsed,
      'has-question': answering,
    }"
    @paste.capture="onPaste"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <div
      v-if="answering || hasReview || showStatus || agent.state.follow[project.id] === false"
      class="agent-dock-heading"
    >
      <strong v-if="answering || hasReview">{{ answering ? '需要你的判断' : '项目对话' }}</strong>
      <span v-if="showStatus" class="agent-live-status" role="status">
        <i v-if="agent.state.running" class="live-dot"></i>
        {{ runningElsewhere ? '其他项目正在运行' : agent.state.label }}
      </span>
      <button
        v-if="hasReview"
        type="button"
        class="button secondary agent-collapse-toggle"
        :aria-expanded="!dockCollapsed"
        @click="dockCollapsed = !dockCollapsed"
      >
        <component :is="dockCollapsed ? ChevronUp : ChevronDown" :size="14" />{{
          dockCollapsed ? '展开项目对话' : '收起项目对话'
        }}
      </button>
      <FollowAgentButton
        v-if="runningHere || agent.state.follow[project.id] === false"
        :following="agent.state.follow[project.id] !== false"
        @resume="$emit('resume')"
      />
    </div>
    <PlanningJobPanel
      v-if="project.planning_job"
      :project-id="project.id"
      :job="project.planning_job"
      @authorize="openJobAuthorization"
    />
    <AgentQuestion
      v-if="project.question"
      :question="project.question"
      :answer="content"
      :disabled="Boolean(operationBlocker)"
      @choose="choose"
    />
    <AgentReviewTray
      ref="reviewTray"
      :project="project"
      :summary="turnSummary"
      :owner="reviewOwner"
      :running="runningHere"
      :disabled="dockCollapsed"
      @locate="$emit('locate', $event)"
    />
    <div
      v-if="attachmentTransfer || failedAttempt"
      v-show="!dockCollapsed"
      ref="review"
      class="agent-review"
      aria-label="对话与本轮变更"
      tabindex="0"
    >
      <AttachmentReceipt :transfer="attachmentTransfer" :selected-ids="referencedAttachmentIds" />
      <div v-if="failedAttempt" class="agent-recovery-card">
        <p v-if="recoveryError" role="status">{{ recoveryError }}</p>
        {{ recoveryNotice }}
        <p class="agent-recovery-request">原请求：{{ failedRequestSnippet }}</p>
        <small v-if="failedAttempt.turnId">请求轮次：{{ failedAttempt.turnId }}</small>
        <details
          v-if="
            !failureRestored ||
            (project.question && project.question.id !== failedAttempt.questionId)
          "
          class="agent-recovery"
        >
          <summary>查看未完成请求（{{ failures.length }}）</summary>
          <p v-if="failedQuestion" class="agent-recovery-question">原问题：{{ failedQuestion }}</p>
          <p>{{ failedAttempt.text }}</p>
          <small v-if="failedAttempt.ids.length">附带 {{ failedAttempt.ids.length }} 份资料</small>
        </details>
        <div class="agent-recovery-actions" role="group" aria-label="未完成请求操作">
          <p v-if="failedCompleteChange">
            完整变更请求未完成，不会重试或重发。请刷新作业状态；如需再次处理，请重新确认完整变更额度。
          </p>
          <button
            v-else
            type="button"
            class="text-button"
            :disabled="Boolean(operationBlocker)"
            @click="retry()"
          >
            重试这条请求
          </button>
          <button
            type="button"
            class="text-button"
            @click="agentDrafts.dismissFailure(project.id, failedAttempt.id)"
          >
            {{ failureRestored ? '关闭提示' : '丢弃这条请求' }}
          </button>
        </div>
      </div>
    </div>
    <div v-if="draggingFiles" class="agent-drop-overlay" aria-live="polite">
      松开以上传并保存到项目 · 本条最多引用6份资料内容
    </div>
    <div v-if="jobSupported || boundedJob" class="planning-job-authorization">
      <label class="planning-job-opt-in"
        ><input v-model="boundedJob" type="checkbox" :disabled="Boolean(operationBlocker)" />
        <span>本次使用有界规划作业（可选）</span></label
      >
      <fieldset v-if="boundedJob" :disabled="Boolean(operationBlocker)">
        <legend>本次作业总额度</legend>
        <label class="planning-job-opt-in"
          ><input v-model="completeChange" type="checkbox" />
          <span>完整变更（最多一次生成 + 一次完整评审）</span></label
        >
        <p v-if="completeChange">
          固定最多 2 次模型调用、393,216 B 累计输入；单次请求最多 163,840 B 输入、98,304 B 输出，180
          秒超时。直接生成完整变更并评审；无自动重试或续跑，失败或评审暂缓即停止。
        </p>
        <div v-else class="planning-job-limits">
          <label
            >最多阶段<input
              v-model.number="jobLimits.max_phases"
              type="number"
              min="1"
              max="3"
              step="1"
          /></label>
          <label
            >最多模型调用<input
              v-model.number="jobLimits.max_calls"
              type="number"
              min="1"
              :max="Math.min(12, 5 * jobLimits.max_phases)"
              step="1"
          /></label>
          <label
            >累计输入字节（B）<input
              v-model.number="jobLimits.max_input_bytes"
              type="number"
              min="1"
              :max="Math.min(1179648, 393216 * jobLimits.max_phases)"
              step="1"
          /></label>
        </div>
        <p v-if="!completeChange">
          每阶段仍最多 5 次调用、393,216 B 输入；本原型总授权最多 3 阶段、12 次调用、1,179,648
          B。不会自动扩额；token 用量可能不完整。
        </p>
        <label class="planning-job-opt-in"
          ><input
            v-model="jobConfirmed"
            type="checkbox"
            :disabled="!jobLimitsValid || !jobSupported"
          />
          <span v-if="completeChange"
            >确认本次完整变更：最多一次生成、一次完整评审，共 2 次调用和 393,216 B 累计输入</span
          >
          <span v-else
            >确认按以上总额度自动推进这次输入，允许最多 {{ jobLimits.max_phases }} 阶段、{{
              jobLimits.max_calls
            }}
            次调用和 {{ jobLimits.max_input_bytes }} B 累计输入</span
          ></label
        >
      </fieldset>
      <p v-if="jobBlocker" class="agent-blocker" role="status">{{ jobBlocker }}</p>
    </div>
    <form style="position: relative" class="agent-input" @submit.prevent="submit()">
      <ComposerEditor
        :key="project.id"
        ref="message"
        v-model="editorDocument"
        :project-id="project.id"
        :catalog="references"
        :catalog-error="referenceError"
        :catalog-warnings="referenceWarnings"
        :explicit-attachment-ids="attachmentIds"
        :input-label="answering ? '你对这个问题的回答' : '发给 Agent 的修改建议'"
        :disabled="runningHere"
        :placeholder="placeholder"
        @submit="submit()"
        @request-catalog="loadReferences"
        @error="editorError = $event"
      />
      <div class="agent-input-footer">
        <AttachmentPicker
          ref="attachmentPicker"
          :project="project"
          :context-key="`${view}:${compact}:${dockCollapsed}`"
          :inline-ids="inlineAttachmentIds"
          v-model="attachmentIds"
          @settings="setPage('settings')"
        />
        <span v-if="counter > 15000" class="agent-counter near">{{ counter }}/16000</span>
        <button
          v-if="runningHere"
          type="button"
          class="stop-agent"
          aria-label="停止当前请求"
          title="停止当前请求"
          @click="agent.stop"
        >
          <Square :size="14" aria-hidden="true" />
        </button>
        <button
          v-else
          type="submit"
          class="send-button"
          :aria-label="
            boundedJob
              ? completeChange
                ? '确认额度并启动完整变更'
                : '确认额度并启动作业'
              : answering
                ? '发送回答'
                : '发送修改建议'
          "
          :disabled="!canSend"
        >
          <ArrowUp :size="20" aria-hidden="true" />
        </button>
      </div>
    </form>
    <p v-if="editorError" class="agent-blocker" role="status">{{ editorError }}</p>
    <div
      v-if="blocker && (content.trim() || runningHere || runningElsewhere || attachmentTransfer)"
      class="agent-blocker"
      role="status"
    >
      {{ blocker.text
      }}<button v-if="blocker.action" type="button" class="text-button" @click="runBlockerAction">
        {{ blocker.action }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.planning-job-authorization {
  font-size: 12px;
  margin-bottom: 8px;
  flex: 0 1 auto;
  min-width: 0;
  min-height: 24px;
  max-height: min(230px, 29dvh);
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
}
.planning-job-opt-in {
  display: flex;
  align-items: flex-start;
  min-width: 0;
  gap: 7px;
}
.planning-job-opt-in input[type='checkbox'] {
  /* Global text-field width/padding must never size these native controls. */
  flex: 0 0 16px;
  width: 16px;
  height: 16px;
  min-width: 16px;
  min-height: 16px;
  padding: 0;
  margin: 2px 0 0;
}
.planning-job-opt-in > span {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
}
.planning-job-authorization fieldset {
  min-width: 0;
  border: 1px solid var(--line, #dbe1e5);
  border-radius: 8px;
  padding: 8px 10px;
  margin-top: 8px;
}
.planning-job-authorization p {
  margin: 7px 0;
}
.planning-job-limits {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.planning-job-limits label {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 110px;
}
.planning-job-limits input {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

/* Recovery actions remain reachable when small-window styles hide passive hints. */
.agent-dock-note.failed {
  display: block;
}

.agent-recovery-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  margin-top: 8px;
}

.agent-recovery p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: none;
  overflow: visible;
}

.agent-recovery-request {
  overflow-wrap: anywhere;
}

/* History yields space before the composer or the graph can leave the viewport. */
.agent-dock {
  display: flex;
  flex-direction: column;
  flex: 0 1 auto;
  min-height: 0;
}
.agent-dock-heading,
.agent-input,
.agent-dock-note {
  flex-shrink: 0;
}
.agent-review {
  flex: 0 1 auto;
  min-height: 0;
  max-height: clamp(80px, calc(100dvh - 740px), 200px);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.agent-review:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 8px;
}
.agent-collapse-toggle {
  margin-left: auto;
  min-height: 30px;
  padding: 5px 9px;
  font-size: 11px;
}
.agent-dock.is-collapsed {
  padding: 0;
}
.agent-dock.is-collapsed .agent-dock-heading {
  margin-bottom: 0;
}
</style>
