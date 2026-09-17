<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Paperclip } from 'lucide-vue-next';
import { useAttachments } from '../../composables/useAttachments';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project } from '../../types';
const props = defineProps<{ project: Project }>();
const selected = defineModel<string[]>({ default: () => [] });
const { state } = useWorkspace();
const { formats, uploading, loadFormats, upload } = useAttachments();
const input = ref<HTMLInputElement>();
onMounted(loadFormats);
async function changed(event: Event) {
  const target = event.target as HTMLInputElement;
  if (target.files)
    selected.value = [...selected.value, ...(await upload(props.project.id, target.files))].slice(
      0,
      6,
    );
  target.value = '';
}
function toggle(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((x) => x !== id)
    : [...selected.value, id].slice(0, 6);
}
</script>
<template>
  <div class="attachment-picker">
    <input
      ref="input"
      class="visually-hidden"
      type="file"
      multiple
      :accept="formats.extensions.join(',')"
      aria-label="上传文档或图片"
      @change="changed"
    />
    <button type="button" class="attachment-add" :disabled="state.busy" @click="input?.click()">
      <Paperclip :size="14" />{{ uploading ? '上传中…' : '添加资料' }}
    </button>
    <button
      v-for="asset in project.attachments"
      :key="asset.id"
      type="button"
      :disabled="state.busy"
      :class="['attachment-chip', { selected: selected.includes(asset.id) }]"
      :aria-pressed="selected.includes(asset.id)"
      @click="toggle(asset.id)"
    >
      {{ asset.media_type.startsWith('image/') ? '▧' : '≡' }} {{ asset.name }}
    </button>
    <small v-if="selected.length">{{ selected.length }}/6 份资料将在发送时提供给当前模型</small>
  </div>
</template>
