<script setup lang="ts">
import { computed } from 'vue';
import type { Message } from '../../types';
import {
  parseComposerDocument,
  referencePrefix,
  referenceKindLabels,
} from '../../lib/composerDocument';
const props = defineProps<{ message: Message }>();
const document = computed(() => parseComposerDocument(props.message.composer_document));
</script>
<template>
  <p class="message-content">
    <template v-if="document"
      ><template v-for="(part, index) in document.parts" :key="index"
        ><span
          v-if="part.type === 'reference'"
          class="message-reference"
          :class="referencePrefix(part.kind) === '@' ? 'material' : 'object'"
          :title="`${referenceKindLabels[part.kind]} · ${part.id} · 发送时记录`"
          >{{ referencePrefix(part.kind) }}{{ part.label }}</span
        ><template v-else>{{ part.text }}</template></template
      ></template
    ><template v-else>{{ message.content }}</template>
  </p>
</template>
