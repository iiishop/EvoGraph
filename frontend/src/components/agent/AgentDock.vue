<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import AgentQuestion from './AgentQuestion.vue';
import AttachmentPicker from '../attachments/AttachmentPicker.vue';
import { ArrowUp, Orbit, Square, CornerDownLeft } from 'lucide-vue-next';
import { useAgent } from '../../composables/useAgent';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project } from '../../types';

const props = defineProps<{ project: Project }>();
const agent = useAgent();
const { state, setPage } = useWorkspace();

const content = ref('');
const attachmentIds = ref<string[]>([]);
const message = ref<HTMLTextAreaElement>();
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

function runBlockerAction() {
  if (!state.settings?.provider) return setPage('settings');
  if (runningElsewhere.value) return setPage('projects');
}

async function submit(text = content.value, questionId = props.project.question?.id) {
  const value = text.trim();
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
  void submit();
}

function choose(option: string) {
  // 选项本身就是一条完整回答，此时输入框通常是空的：不能用 canSend（它要求输入框非空）
  if (blocker.value) return;
  void submit(option);
}
</script>
<template>
  <section class="agent-dock">
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
    <form class="agent-input" @submit.prevent="submit()">
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
        @keydown.enter.exact.prevent="onEnter"
      />
      <div class="agent-input-footer">
        <AttachmentPicker :project="project" v-model="attachmentIds" />
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
