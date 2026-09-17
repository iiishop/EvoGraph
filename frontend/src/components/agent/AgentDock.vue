<script setup lang="ts">
import { ref, watch } from 'vue';
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
watch(
  () => props.project.id,
  () => {
    content.value = '';
    attachmentIds.value = [];
  },
);
async function submit() {
  if (!content.value.trim() || agent.state.running || state.busy || !state.settings?.provider)
    return;
  const text = content.value;
  content.value = '';
  const ids = [...attachmentIds.value];
  attachmentIds.value = [];
  await agent.send(props.project.id, text, props.project.question?.id, ids);
}
</script>
<template>
  <section class="agent-dock">
    <div class="agent-dock-heading">
      <span class="agent-symbol"><Orbit :size="17" /></span><strong>描述变化，图会随之演化</strong
      ><span class="agent-mode">{{ state.settings?.provider?.config.model || '未连接模型' }}</span
      ><span class="agent-live-status" role="status"
        ><i v-if="agent.state.running" class="live-dot"></i
        >{{ agent.state.projectId === project.id ? agent.state.label : '' }}</span
      >
    </div>
    <form class="agent-input" @submit.prevent="submit">
      <AgentQuestion
        v-if="project.question"
        :question="project.question"
        :answer="content"
        :disabled="agent.state.running"
        @choose="content = $event"
      />
      <textarea
        v-model="content"
        aria-label="修改项目的建议"
        :disabled="agent.state.running"
        rows="2"
        maxlength="16000"
        :placeholder="
          project.question
            ? '在这里回答，或选择上方选项…'
            : '例如：增加邮箱验证，把登录方式改成邮箱 + 密码…'
        "
        @keydown.enter.exact.prevent="submit"
      />
      <AttachmentPicker :project="project" v-model="attachmentIds" />
      <div class="agent-input-footer">
        <span><CornerDownLeft :size="12" /> Enter 发送 · Shift + Enter 换行</span
        ><button
          v-if="!state.settings?.provider"
          type="button"
          class="text-button"
          @click="setPage('settings')"
        >
          配置 Provider</button
        ><button v-if="agent.state.running" class="stop-agent" type="button" @click="agent.stop">
          <Square :size="13" />停止</button
        ><button
          v-else
          class="send-button"
          aria-label="发送修改建议"
          :disabled="!content.trim() || !state.settings?.provider || state.busy"
        >
          <ArrowUp :size="19" />
        </button>
      </div>
    </form>
    <div class="agent-dock-note">直接修改当前图 · 缺少信息时会向你提问 · 不会自动修改仓库代码</div>
  </section>
</template>
