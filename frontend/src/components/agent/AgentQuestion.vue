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
        @click="$emit('choose', option)"
      >
        {{ option }}
      </button>
    </div>
  </fieldset>
</template>
