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

async function changed(event: Event) {
  const target = event.target as HTMLInputElement;
  if (target.files) {
    const uploaded = await upload(props.project.id, target.files);
    const room = LIMIT - selected.value.length;
    selected.value = [...selected.value, ...uploaded.slice(0, Math.max(room, 0))];
    overflow.value = Math.max(uploaded.length - room, 0);
  }
  target.value = '';
}
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
