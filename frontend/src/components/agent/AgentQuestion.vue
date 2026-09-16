<script setup lang="ts">
import { ref, watch } from 'vue';
import { CircleHelp, ArrowUpRight } from 'lucide-vue-next';
import type { PendingQuestion } from '../../types';
import { useAgent } from '../../composables/useAgent';
const props = defineProps<{ question: PendingQuestion; projectId: string }>();
const { state, send } = useAgent();
const answer = ref('');
watch(
  () => props.question.id,
  () => {
    answer.value = '';
  },
);
</script>
<template>
  <form class="agent-question" @submit.prevent="send(projectId, answer, question.id)">
    <div class="question-heading"><CircleHelp :size="18" /><span>需要你补充一个信息</span></div>
    <h3>{{ question.prompt }}</h3>
    <p v-if="question.context">{{ question.context }}</p>
    <div class="question-options">
      <button
        v-for="option in question.options"
        :key="option"
        type="button"
        :class="{ selected: answer === option }"
        :disabled="state.running"
        @click="answer = option"
      >
        {{ option }}
      </button>
    </div>
    <div class="question-answer">
      <input
        v-model="answer"
        aria-label="回答 Agent 的问题"
        placeholder="选择上方选项，或输入你的回答…"
        :disabled="state.running"
        maxlength="16000"
      /><button class="button primary" :disabled="!answer.trim() || state.running">
        继续 <ArrowUpRight :size="14" />
      </button>
    </div>
  </form>
</template>
