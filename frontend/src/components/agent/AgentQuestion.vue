<script setup lang="ts">
import type { PendingQuestion } from '../../types';
defineProps<{ question: PendingQuestion; answer: string; disabled: boolean }>();
defineEmits<{ choose: [answer: string] }>();
</script>
<template>
  <fieldset class="inline-question">
    <legend>{{ question.prompt }}</legend>
    <p v-if="question.context">{{ question.context }}</p>
    <div class="question-options">
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
  </fieldset>
</template>
