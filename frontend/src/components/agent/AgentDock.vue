<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import AgentQuestion from './AgentQuestion.vue';
import AgentTurnSummary from './AgentTurnSummary.vue';
import { latestTurnSummary } from '../../lib/turnSummary';
import { planAgentRetry, waitForRetryRead } from '../../lib/agentRetry';
import FollowAgentButton from '../graph/FollowAgentButton.vue';
import AttachmentPicker from '../attachments/AttachmentPicker.vue';
import ReferenceMentionPicker from './ReferenceMentionPicker.vue';
import { ArrowUp, Orbit, Square, CornerDownLeft, ChevronDown, ChevronUp } from 'lucide-vue-next';
import { command } from '../../api/client';
import { useAgent } from '../../composables/useAgent';
import {
  agentDrafts,
  useAgentDraft,
  type DraftAttempt,
  type FailedDraft,
} from '../../composables/useAgentDrafts';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project, ReferenceItem } from '../../types';

const props = defineProps<{ project: Project; compact?: boolean }>();
defineEmits<{ resume: [] }>();
const conversationOpen = ref(false);
const dockCollapsed = ref(false);
const latestReply = computed(() =>
  props.project.messages.filter((item) => item.role === 'assistant').at(-1),
);
const turnSummary = computed(() => latestTurnSummary(props.project));
const agent = useAgent();
const { state, setPage, setError, selectProject, applyProject } = useWorkspace();
const {
  content,
  attachmentIds,
  failures,
  pending,
  failureRestored,
  restoredFailure,
  recoveryError,
} = useAgentDraft(() => props.project.id);
const failedAttempt = computed(() => failures.value[0]);
const failedQuestion = computed(() => {
  const prompt = failedAttempt.value?.question?.prompt ?? '';
  return prompt.length > 240 ? `${prompt.slice(0, 240)}…` : prompt;
});
let mounted = true;
onUnmounted(() => {
  mounted = false;
});
const message = ref<HTMLTextAreaElement>();
const attachmentPicker = ref<InstanceType<typeof AttachmentPicker>>();
const mentionPicker = ref<InstanceType<typeof ReferenceMentionPicker>>();
const dragDepth = ref(0);
const references = ref<ReferenceItem[]>([]);
const referencesLoadedFor = ref('');
const mentionOpen = ref(false);
const mentionQuery = ref('');
const mentionStart = ref(0);
const mentionCursor = ref(0);
watch(
  () => props.project.id,
  () => {
    references.value = [];
    referencesLoadedFor.value = '';
    mentionOpen.value = false;
    conversationOpen.value = false;
  },
);

const runningHere = computed(
  () => agent.state.running && agent.state.projectId === props.project.id,
);
const runningElsewhere = computed(
  () => agent.state.running && agent.state.projectId !== props.project.id,
);
const answering = computed(() => Boolean(props.project.question));
watch(
  () => props.compact,
  (compact) => {
    dockCollapsed.value = Boolean(
      compact && !content.value.trim() && !answering.value && !runningHere.value,
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
const modelName = computed(() => state.settings?.provider?.config.model || '未连接模型');

// Why the send button is dead, spelled out instead of silently ignored.
const blocker = computed(() => {
  if (!state.settings?.provider) return { text: '未配置模型，无法发送', action: '配置 Provider' };
  if (runningElsewhere.value) return { text: 'Agent 正在其他项目运行', action: '查看' };
  // 运行中时不再给提示行挂"停止"：右侧停止按钮就在同一行，重复一个操作
  if (runningHere.value) return { text: '上一轮仍在处理，可随时停止', action: '' };
  if (state.busy) return { text: '正在处理上一步操作', action: '' };
  if (pending.value) return { text: '正在确认上一条请求的结果', action: '' };
  return null;
});
const canSend = computed(() => Boolean(content.value.trim()) && !blocker.value);

const sampleMilestone = computed(() => props.project.milestones?.[0]?.title ?? '第一个里程碑');
const placeholder = computed(() =>
  answering.value
    ? '也可以在这里自己写回答，或直接点上方选项…'
    : `例如：把「${sampleMilestone.value}」拆成两个里程碑`,
);
const counter = computed(() => content.value.length);
watch(
  () => [props.project.repository, props.project.attachments],
  () => {
    referencesLoadedFor.value = '';
    if (mentionOpen.value) void loadReferences();
  },
);

function runBlockerAction() {
  if (!state.settings?.provider) return setPage('settings');
  if (runningElsewhere.value) return selectProject(agent.state.projectId);
}

async function deliver(attempt: DraftAttempt) {
  let delivered = false;
  try {
    delivered = await agent.send(
      attempt.projectId,
      attempt.request?.text ?? attempt.text.trim(),
      attempt.request ? attempt.request.questionId : attempt.questionId,
      attempt.ids,
      attempt.request ? attempt.request.verificationMilestone : attempt.verificationMilestone,
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
  mentionOpen.value = false;
  // Keep the exact draft for recovery; trim only the submitted payload.
  if (!text.trim() || blocker.value) return;
  const restored = restoredFailure.value;
  if (!chosenOption && restored && text === content.value) {
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
  if (attempt) await deliver(attempt);
}

async function retry(failure: FailedDraft | undefined = failedAttempt.value, fromComposer = false) {
  if (!failure || blocker.value) return;
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
      if (plan.differentQuestion) agentDrafts.detachRestored(projectId, failure.id);
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

function onEnter(event: KeyboardEvent) {
  // Chinese IMEs dispatch Enter while composing; sending there would ship half
  // a sentence, so the composing keystroke never reaches submit.
  if (event.isComposing || event.keyCode === 229) return;
  if (mentionOpen.value) {
    mentionPicker.value?.chooseActive();
    return;
  }
  void submit();
}

function onKeydown(event: KeyboardEvent) {
  if (!mentionOpen.value) return;
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    mentionPicker.value?.move(1);
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    mentionPicker.value?.move(-1);
  } else if (event.key === 'Escape') {
    event.preventDefault();
    mentionOpen.value = false;
  }
}

async function loadReferences() {
  if (referencesLoadedFor.value === props.project.id) return;
  const projectId = props.project.id;
  try {
    const result = await command<{ items: ReferenceItem[] }>('references.list', {
      project_id: projectId,
    });
    if (props.project.id === projectId) {
      references.value = result.items;
      referencesLoadedFor.value = projectId;
    }
  } catch (error) {
    setError(error instanceof Error ? error.message : '资料列表加载失败');
  }
}

function updateMentionState() {
  const input = message.value;
  if (!input) return;
  const cursor = input.selectionStart ?? content.value.length;
  const prefix = content.value.slice(0, cursor);
  const match = prefix.match(/@([^\s@]*)$/u);
  if (!match) {
    mentionOpen.value = false;
    return;
  }
  mentionQuery.value = match[1];
  mentionStart.value = cursor - match[1].length - 1;
  mentionCursor.value = cursor;
  mentionOpen.value = true;
  void loadReferences();
}

async function insertReference(item: ReferenceItem) {
  const input = message.value;
  if (!input) return;
  const cursor = mentionCursor.value;
  const before = content.value.slice(0, mentionStart.value);
  const after = content.value.slice(cursor);
  if (
    item.kind === 'attachment' &&
    !attachmentIds.value.includes(item.id) &&
    attachmentIds.value.length >= 6
  ) {
    setError('每次最多引用 6 份资料，请先取消一份');
    return;
  }
  const token = item.kind === 'repository' ? `@[仓库文件:${item.path}]` : `@[${item.name}]`;
  content.value = `${before}${token} ${after}`;
  if (item.kind === 'attachment' && !attachmentIds.value.includes(item.id)) {
    attachmentIds.value = [...attachmentIds.value, item.id];
  }
  mentionOpen.value = false;
  await nextTick();
  const nextCursor = before.length + token.length + 1;
  input.focus();
  input.setSelectionRange(nextCursor, nextCursor);
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
  if (blocker.value) return;
  void submit(option, props.project.question?.id, true);
}
</script>
<template>
  <section
    class="agent-dock"
    :class="{ 'is-drop-target': draggingFiles, 'is-collapsed': dockCollapsed }"
    @paste.capture="onPaste"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <div class="agent-dock-heading">
      <span class="agent-symbol"><Orbit :size="17" aria-hidden="true" /></span
      ><strong>{{ answering ? '需要你的判断' : '调整项目规划' }}</strong
      ><span class="agent-scope">{{ answering ? '回答一个问题' : '直接修改当前图' }}</span
      ><button
        type="button"
        class="agent-mode"
        :title="state.settings?.provider ? '切换模型（前往设置）' : '尚未配置模型'"
        @click="setPage('settings')"
      >
        {{ modelName }}</button
      ><span class="agent-live-status" role="status"
        ><i v-if="agent.state.running" class="live-dot"></i
        ><template v-if="runningElsewhere">其他项目正在运行</template
        ><template v-else-if="agent.state.projectId === project.id">{{
          agent.state.label
        }}</template></span
      >
      <button
        v-if="compact"
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
    <div
      v-if="project.messages.length || (turnSummary && !runningHere)"
      v-show="!dockCollapsed"
      class="agent-review"
      aria-label="对话与本轮变更"
      tabindex="0"
    >
      <details
        v-if="project.messages.length"
        v-show="!dockCollapsed"
        class="agent-conversation"
        :open="conversationOpen"
        @toggle="conversationOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary>
          <span>{{ latestReply ? '最近回复' : '对话记录' }}</span
          ><span class="reply-preview">{{ latestReply?.content || '查看已发送的请求' }}</span>
        </summary>
        <div class="agent-conversation-scroll" aria-label="项目对话记录">
          <article v-for="item in project.messages" :key="item.id" :class="item.role">
            <strong>{{ item.role === 'assistant' ? 'Agent' : '你' }}</strong>
            <p>{{ item.content }}</p>
          </article>
        </div>
      </details>
      <AgentTurnSummary
        v-if="turnSummary && !runningHere"
        v-show="!dockCollapsed"
        :key="`${project.id}-${turnSummary.turn_id}`"
        :summary="turnSummary"
        :milestones="[...project.milestones, ...(project.source_milestones ?? [])]"
      />
    </div>
    <div v-if="draggingFiles" class="agent-drop-overlay" aria-live="polite">
      松开以上传文档或图片
    </div>
    <form
      v-show="!dockCollapsed"
      style="position: relative"
      class="agent-input"
      @submit.prevent="submit()"
    >
      <ReferenceMentionPicker
        v-if="mentionOpen"
        ref="mentionPicker"
        :items="references"
        :query="mentionQuery"
        @select="insertReference"
      />
      <AgentQuestion
        v-if="project.question"
        :question="project.question"
        :answer="content"
        :disabled="agent.state.running"
        @choose="choose"
      />
      <textarea
        id="agent-message"
        ref="message"
        v-model="content"
        :aria-label="answering ? '你对这个问题的回答' : '发给 Agent 的修改建议'"
        :disabled="runningHere"
        rows="2"
        maxlength="16000"
        :placeholder="placeholder"
        @input="updateMentionState"
        @click="updateMentionState"
        @keyup.left="updateMentionState"
        @keyup.right="updateMentionState"
        @blur="mentionOpen = false"
        @keydown="onKeydown"
        @keydown.enter.exact.prevent="onEnter"
      />
      <div class="agent-input-footer">
        <AttachmentPicker ref="attachmentPicker" :project="project" v-model="attachmentIds" />
        <span v-if="blocker" class="agent-blocker" role="status"
          >{{ blocker.text
          }}<button
            v-if="blocker.action"
            type="button"
            class="text-button"
            @click="runBlockerAction"
          >
            {{ blocker.action }}
          </button></span
        ><span v-else class="agent-hint"
          ><CornerDownLeft :size="12" aria-hidden="true" /> Enter 发送 · Shift + Enter 换行</span
        ><span v-if="counter > 200" class="agent-counter" :class="{ near: counter > 15000 }">{{
          counter
        }}</span
        ><button v-if="runningHere" type="button" class="stop-agent" @click="agent.stop">
          <Square :size="13" aria-hidden="true" />停止</button
        ><button
          v-else
          type="submit"
          class="send-button"
          :aria-label="answering ? '发送回答' : '发送修改建议'"
          :disabled="!canSend"
        >
          <ArrowUp :size="19" aria-hidden="true" />
        </button>
      </div>
    </form>
    <div v-show="!dockCollapsed" class="agent-dock-note" :class="{ failed: failedAttempt }">
      <template v-if="failedAttempt">
        <p v-if="recoveryError" role="status">{{ recoveryError }}</p>
        {{
          failureRestored
            ? '本轮未完成，内容已放回输入框；已保存的修改会保留。'
            : '未完成请求已保留，当前草稿未改动；已保存的修改会保留。'
        }}
        <details v-if="!failureRestored" class="agent-recovery">
          <summary>查看未完成请求（{{ failures.length }}）</summary>
          <p v-if="failedQuestion" class="agent-recovery-question">原问题：{{ failedQuestion }}</p>
          <p>{{ failedAttempt.text }}</p>
          <small v-if="failedAttempt.ids.length">附带 {{ failedAttempt.ids.length }} 份资料</small>
        </details>
        <button type="button" class="text-button" :disabled="Boolean(blocker)" @click="retry()">
          重试这条请求
        </button>
        <button
          type="button"
          class="text-button"
          @click="agentDrafts.dismissFailure(project.id, failedAttempt.id)"
        >
          {{ failureRestored ? '关闭提示' : '丢弃这条请求' }}
        </button> </template
      ><template v-else>缺少信息时会向你提问 · 不会自动修改仓库代码</template>
    </div>
  </section>
</template>

<style scoped>
/* Recovery actions remain reachable when small-window styles hide passive hints. */
.agent-dock-note.failed {
  display: block;
}

.agent-recovery p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 120px;
  overflow-y: auto;
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
  min-height: 36px;
  max-height: min(220px, 30dvh);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.agent-review:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 8px;
}
/* One review scroller, rather than nested transcript/receipt scroll traps. */
.agent-review .agent-conversation-scroll,
.agent-review :deep(.turn-summary-scroll) {
  max-height: none;
  overflow: visible;
}

.agent-collapse-toggle {
  margin-left: auto;
  min-height: 30px;
  padding: 5px 9px;
  font-size: 11px;
}
.agent-dock.is-collapsed {
  padding-top: 8px;
  padding-bottom: 8px;
}
.agent-dock.is-collapsed .agent-dock-heading {
  margin-bottom: 0;
}
</style>
