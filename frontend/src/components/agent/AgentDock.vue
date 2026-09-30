<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import AgentQuestion from './AgentQuestion.vue';
import AttachmentPicker from '../attachments/AttachmentPicker.vue';
import ReferenceMentionPicker from './ReferenceMentionPicker.vue';
import { ArrowUp, Orbit, Square, CornerDownLeft } from 'lucide-vue-next';
import { command } from '../../api/client';
import { useAgent } from '../../composables/useAgent';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project, ReferenceItem } from '../../types';

const props = defineProps<{ project: Project }>();
const agent = useAgent();
const { state, setPage, setError } = useWorkspace();

const content = ref('');
const attachmentIds = ref<string[]>([]);
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
// Drafts survive a trip to another project and back.
const drafts = new Map<string, { text: string; ids: string[] }>();
const failedAttempt = ref<{ text: string; ids: string[]; questionId?: string } | null>(null);

watch(
  () => props.project.id,
  (next, previous) => {
    if (previous) drafts.set(previous, { text: content.value, ids: [...attachmentIds.value] });
    const saved = drafts.get(next);
    content.value = saved?.text ?? '';
    attachmentIds.value = saved?.ids ?? [];
    failedAttempt.value = null;
    references.value = [];
    referencesLoadedFor.value = '';
    mentionOpen.value = false;
  },
);

const runningHere = computed(
  () => agent.state.running && agent.state.projectId === props.project.id,
);
const runningElsewhere = computed(
  () => agent.state.running && agent.state.projectId !== props.project.id,
);
const answering = computed(() => Boolean(props.project.question));
const modelName = computed(() => state.settings?.provider?.config.model || '未连接模型');

// Why the send button is dead, spelled out instead of silently ignored.
const blocker = computed(() => {
  if (!state.settings?.provider) return { text: '未配置模型，无法发送', action: '配置 Provider' };
  if (runningElsewhere.value) return { text: 'Agent 正在其他项目运行', action: '查看' };
  // 运行中时不再给提示行挂"停止"：右侧停止按钮就在同一行，重复一个操作
  if (runningHere.value) return { text: '上一轮仍在处理，可随时停止', action: '' };
  if (state.busy) return { text: '正在处理上一步操作', action: '' };
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
  if (runningElsewhere.value) return setPage('projects');
}

async function submit(text = content.value, questionId = props.project.question?.id) {
  const value = text.trim();
  mentionOpen.value = false;
  // 判的是"这一条内容能不能发"，而不是"输入框里有没有东西"：点选项走的就是这条路
  if (!value || blocker.value) return;
  const ids = [...attachmentIds.value];
  failedAttempt.value = null;
  content.value = '';
  attachmentIds.value = [];
  const ok = await agent.send(props.project.id, value, questionId, ids);
  if (ok) return;
  // Hand the text back rather than dropping a paragraph the user just wrote.
  const attempt = { text: value, ids, questionId };
  failedAttempt.value = attempt;
  content.value = attempt.text;
  attachmentIds.value = attempt.ids;
  await nextTick();
  message.value?.focus();
}

async function retry() {
  const attempt = failedAttempt.value;
  if (!attempt) return;
  content.value = attempt.text;
  attachmentIds.value = attempt.ids;
  await nextTick();
  await submit(attempt.text, attempt.questionId);
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
  void submit(option);
}
</script>
<template>
  <section
    class="agent-dock"
    :class="{ 'is-drop-target': draggingFiles }"
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
        ><template v-else-if="runningHere">{{ agent.state.label }}</template></span
      >
    </div>
    <div v-if="draggingFiles" class="agent-drop-overlay" aria-live="polite">
      松开以上传文档或图片
    </div>
    <form style="position: relative" class="agent-input" @submit.prevent="submit()">
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
        :disabled="agent.state.running"
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
          type="button"
          class="send-button"
          :aria-label="answering ? '发送回答' : '发送修改建议'"
          :disabled="!canSend"
        >
          <ArrowUp :size="19" aria-hidden="true" />
        </button>
      </div>
    </form>
    <div class="agent-dock-note" :class="{ failed: failedAttempt }">
      <template v-if="failedAttempt"
        >发送未完成，内容已放回输入框。<button type="button" class="text-button" @click="retry">
          重试
        </button></template
      ><template v-else>缺少信息时会向你提问 · 不会自动修改仓库代码</template>
    </div>
  </section>
</template>
