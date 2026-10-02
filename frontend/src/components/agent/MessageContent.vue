<script setup lang="ts">
import { computed } from 'vue';
import MarkdownContent from './MarkdownContent';
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
  <div v-if="message.role === 'assistant'" class="message-content markdown-content">
    <MarkdownContent :content="message.content" />
  </div>
  <p v-else class="message-content">
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

<style scoped>
.markdown-content {
  white-space: normal;
  overflow-wrap: anywhere;
  min-width: 0;
  user-select: text;
}
.markdown-content :deep(.markdown-body > :first-child) {
  margin-top: 0;
}
.markdown-content :deep(.markdown-body > :last-child) {
  margin-bottom: 0;
}
.markdown-content :deep(p) {
  margin: 0.65em 0;
}
.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4),
.markdown-content :deep(h5),
.markdown-content :deep(h6) {
  margin: 1.1em 0 0.45em;
  font-weight: 650;
  line-height: 1.4;
}
.markdown-content :deep(h1) {
  font-size: 1.5em;
}
.markdown-content :deep(h2) {
  font-size: 1.3em;
}
.markdown-content :deep(h3) {
  font-size: 1.15em;
}
.markdown-content :deep(h4),
.markdown-content :deep(h5),
.markdown-content :deep(h6) {
  font-size: 1em;
}
.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin: 0.65em 0;
  padding-left: 1.8em;
}
.markdown-content :deep(li) {
  margin: 0.25em 0;
}
.markdown-content :deep(blockquote) {
  margin: 0.75em 0;
  padding: 0.1em 1em;
  border-left: 3px solid var(--line, #d8e1e7);
  color: var(--text-secondary, #617383);
}
.markdown-content :deep(a) {
  color: var(--accent, #376d83);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.markdown-content :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.95em;
  background: #edf2f5;
  border-radius: 4px;
  padding: 0.12em 0.3em;
}
.markdown-content :deep(pre) {
  max-width: 100%;
  overflow-x: auto;
  padding: 12px;
  border: 1px solid var(--line, #d8e1e7);
  border-radius: 8px;
  background: #f4f7f9;
  white-space: pre;
  overflow-wrap: normal;
  tab-size: 2;
}
.markdown-content :deep(pre code) {
  padding: 0;
  background: none;
  border-radius: 0;
}
.markdown-content :deep(.markdown-table-scroll) {
  max-width: 100%;
  overflow-x: auto;
  margin: 0.75em 0;
}
.markdown-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  font-size: inherit;
}
.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid var(--line, #d8e1e7);
  padding: 6px 10px;
  text-align: left;
  min-width: 80px;
}
.markdown-content :deep(th) {
  background: #f4f7f9;
  font-weight: 650;
}
.markdown-content :deep(hr) {
  border: 0;
  border-top: 1px solid var(--line, #d8e1e7);
  margin: 1em 0;
}
.markdown-content :deep(.markdown-image) {
  color: var(--text-secondary, #617383);
}
.markdown-content :deep(a:focus-visible),
.markdown-content :deep(pre:focus-visible),
.markdown-content :deep(.markdown-table-scroll:focus-visible) {
  outline: 2px solid var(--accent, #376d83);
  outline-offset: 2px;
}
</style>
