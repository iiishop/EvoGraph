<script setup lang="ts">
import type { PendingQuestion } from '../../types';
defineProps<{ question: PendingQuestion; answer: string; disabled: boolean }>();
defineEmits<{ choose: [answer: string] }>();
</script>
<template>
  <fieldset class="inline-question" :aria-describedby="`question-copy-${question.id}`">
    <legend class="visually-hidden">当前问题</legend>
    <div class="question-layout">
      <div
        :id="`question-copy-${question.id}`"
        class="question-copy"
        tabindex="0"
        role="region"
        aria-label="完整问题说明"
      >
        <p class="question-prompt">{{ question.prompt }}</p>
        <p v-if="question.context">{{ question.context }}</p>
      </div>
      <div class="question-options" role="group" aria-label="可选回答">
        <button
          v-for="option in question.options"
          :key="option"
          type="button"
          :disabled="disabled"
          :class="{ selected: answer === option }"
          :aria-pressed="answer === option"
          title="点击即作为你的回答发送"
          @click="$emit('choose', option)"
        >
          {{ option }}
        </button>
      </div>
      <small class="question-hint">点选项会直接发送这条回答；想补充说明就在下面自己写。</small>
    </div>
  </fieldset>
</template>

<style scoped>
.question-layout {
  display: grid;
  grid-template-rows: minmax(32px, 1fr) auto auto;
  gap: 6px;
  height: clamp(128px, calc(100dvh - 600px), 188px);
  min-width: 0;
}
.question-copy {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  overflow-wrap: anywhere;
  padding-right: 5px;
}
.question-copy .question-prompt {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.6;
}
.question-copy p + p {
  margin-top: 6px;
}
.question-options {
  margin-top: 0;
  max-height: 70px;
  overflow-y: auto;
  overscroll-behavior: contain;
  gap: 6px;
}
.question-options button {
  min-height: 32px;
  padding: 6px 10px;
}
.question-hint {
  margin-top: 0;
  font-size: 11px;
  line-height: 1.5;
}
.question-copy:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
  border-radius: 5px;
}
</style>
