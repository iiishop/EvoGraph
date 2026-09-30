<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { LoaderCircle, Plus } from 'lucide-vue-next';
import { useAttachments } from '../../composables/useAttachments';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project } from '../../types';

const props = defineProps<{ project: Project }>();
const selected = defineModel<string[]>({ default: () => [] });
const { state } = useWorkspace();
const { formats, uploading, loadFormats, upload } = useAttachments();
const input = ref<HTMLInputElement>();
const overflow = ref(0);
const LIMIT = 6;
const atLimit = computed(() => selected.value.length >= LIMIT);
onMounted(loadFormats);

function normalizedFiles(files: FileList | File[]) {
  return Array.from(files).map((file, index) => {
    const extension = file.name.includes('.')
      ? `.${file.name.split('.').pop()?.toLowerCase()}`
      : '';
    const known = formats.value.extensions.includes(extension);
    if (file.name && known) return file;
    const suffixByMime: Record<string, string> = {
      'image/png': 'png',
      'image/jpeg': 'jpg',
      'image/webp': 'webp',
      'application/pdf': 'pdf',
      'text/plain': 'txt',
      'text/markdown': 'md',
      'text/csv': 'csv',
      'application/json': 'json',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    };
    const suffix =
      suffixByMime[file.type] || file.type.split('/')[1]?.replace(/[^a-z0-9]/gi, '') || 'bin';
    return new File([file], `pasted-${Date.now()}-${index}.${suffix}`, {
      type: file.type,
      lastModified: file.lastModified,
    });
  });
}

async function acceptFiles(files: FileList | File[]) {
  const normalized = normalizedFiles(files);
  if (!normalized.length || state.busy || uploading.value) return;
  const projectId = props.project.id;
  const uploaded = [...new Set(await upload(projectId, normalized))].filter(
    (id) => !selected.value.includes(id),
  );
  if (props.project.id !== projectId) return;
  const room = LIMIT - selected.value.length;
  selected.value = [...selected.value, ...uploaded.slice(0, Math.max(room, 0))];
  overflow.value = Math.max(uploaded.length - Math.max(room, 0), 0);
}

async function changed(event: Event) {
  const target = event.target as HTMLInputElement;
  if (target.files) await acceptFiles(target.files);
  target.value = '';
}

defineExpose({ acceptFiles });
function toggle(id: string) {
  if (selected.value.includes(id)) {
    selected.value = selected.value.filter((x) => x !== id);
    overflow.value = 0;
    return;
  }
  if (atLimit.value) return;
  selected.value = [...selected.value, id];
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
    <button
      type="button"
      class="attachment-add"
      :disabled="state.busy || uploading"
      :aria-label="uploading ? '正在上传资料' : '添加资料'"
      :title="
        atLimit
          ? `最多 ${LIMIT} 份，取消一份才能再加`
          : `上传文档或图片，选中的资料会随本条消息发给模型（最多 ${LIMIT} 份）`
      "
      @click="input?.click()"
    >
      <LoaderCircle v-if="uploading" :size="16" class="spinning" aria-hidden="true" />
      <Plus v-else :size="17" aria-hidden="true" />
    </button>
    <button
      v-for="asset in project.attachments"
      :key="asset.id"
      type="button"
      :disabled="state.busy || (atLimit && !selected.includes(asset.id))"
      :class="['attachment-chip', { selected: selected.includes(asset.id) }]"
      :aria-pressed="selected.includes(asset.id)"
      :title="atLimit && !selected.includes(asset.id) ? '最多 6 份，取消一份才能再加' : asset.name"
      @click="toggle(asset.id)"
    >
      {{ asset.media_type.startsWith('image/') ? '▧' : '≡' }} {{ asset.name }}
    </button>
    <small
      v-if="selected.length"
      class="attachment-count"
      :title="`${selected.length}/${LIMIT} 份资料将在发送时提供给当前模型${atLimit ? '，已满' : ''}`"
      >{{ selected.length }}/{{ LIMIT }}</small
    ><small v-if="overflow" class="attachment-overflow" :title="`超出 ${LIMIT} 份的部分没有上传`"
      >+{{ overflow }}</small
    >
  </div>
</template>
