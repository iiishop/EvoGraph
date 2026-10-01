<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { LoaderCircle, Plus } from 'lucide-vue-next';
import { useAttachments } from '../../composables/useAttachments';
import { useWorkspace } from '../../composables/useWorkspace';
import { useNotifications } from '../../composables/useNotifications';
import { agentDrafts, useAgentDraft } from '../../composables/useAgentDrafts';
import type { Project } from '../../types';

const props = defineProps<{ project: Project }>();
const selected = defineModel<string[]>({ default: () => [] });
const { state } = useWorkspace();
const { formats, formatError, loadFormats, uploadBatch } = useAttachments();
const { confirmedAttachments, attachmentTransfer } = useAgentDraft(() => props.project.id);
const uploading = computed(() =>
  Boolean(attachmentTransfer.value && attachmentTransfer.value.phase !== 'done'),
);
const report = computed(() => attachmentTransfer.value?.report);
const available = computed(() => [
  ...new Map(
    [
      ...(report.value?.assets ?? []),
      ...confirmedAttachments.value,
      ...props.project.attachments,
    ].map((asset) => [asset.id, asset]),
  ).values(),
]);
const referenced = computed(
  () => report.value?.assets.filter((asset) => selected.value.includes(asset.id)).length ?? 0,
);
const input = ref<HTMLInputElement>();
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
  const chosen = Array.from(files);
  if (!chosen.length || state.busy || uploading.value) return;
  const projectId = props.project.id;
  const projectName = props.project.name || projectId;
  // Reserve the original draft incarnation before the first await. A format
  // read must not admit an old selection into a deleted/restored project.
  const transfer = agentDrafts.beginAttachmentTransfer(projectId, chosen.length, 'preparing');
  if (!transfer) return;
  const notUploaded = (message: string) =>
    agentDrafts.finishAttachmentTransfer(transfer, {
      assets: [],
      confirmedFiles: [],
      refresh: 'not_needed',
      failure: { name: chosen[0].name, status: 'not_uploaded', message },
      unattempted: chosen.slice(1).map((file) => file.name),
    });
  const ready = formats.value.extensions.length > 0 || (await loadFormats());
  if (!agentDrafts.attachmentTransferIsCurrent(transfer)) {
    useNotifications().push(
      `「${projectName}」原项目已删除或改变，所选文件尚未上传，请重新选择项目和文件`,
    );
    return;
  }
  if (!ready) {
    notUploaded('格式信息尚未就绪，文件尚未上传，请重试后重新选择');
    return;
  }
  if (state.busy) {
    notUploaded('另一项操作正在进行，文件尚未上传，请稍后重新选择');
    if (state.project?.id !== projectId)
      useNotifications().push(`「${projectName}」所选文件尚未上传，请稍后重新选择`);
    return;
  }
  const normalized = normalizedFiles(chosen);
  const result = await uploadBatch(projectId, normalized, {
    isCurrent: () => agentDrafts.attachmentTransferIsCurrent(transfer),
    onConfirmed: (asset) => {
      agentDrafts.confirmAttachment(transfer, asset, LIMIT);
    },
    onPhase: (phase, completed) => agentDrafts.attachmentPhase(transfer, phase, completed),
    onComplete: (outcome) => agentDrafts.finishAttachmentTransfer(transfer, outcome),
  });
  if (!result) notUploaded('另一项操作正在进行，文件尚未上传，请稍后重新选择');
}

async function changed(event: Event) {
  const target = event.target as HTMLInputElement;
  try {
    if (target.files) await acceptFiles(target.files);
  } finally {
    target.value = '';
  }
}

defineExpose({ acceptFiles });
function toggle(id: string) {
  if (selected.value.includes(id)) {
    selected.value = selected.value.filter((x) => x !== id);
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
    <small class="attachment-upload-help"
      >所选文件上传成功后都会保存到项目；本条消息最多引用 {{ LIMIT }} 份资料内容</small
    >
    <button
      type="button"
      class="attachment-add"
      :disabled="state.busy || uploading"
      :aria-label="uploading ? '正在处理资料' : '添加资料'"
      :title="
        atLimit
          ? `本条引用已满；所选文件上传成功后仍会保存到当前项目，最多引用 ${LIMIT} 份资料内容`
          : `所选文件上传成功后都会保存到当前项目；每条消息最多引用 ${LIMIT} 份资料内容`
      "
      @click="input?.click()"
    >
      <LoaderCircle v-if="uploading" :size="16" class="spinning" aria-hidden="true" />
      <Plus v-else :size="17" aria-hidden="true" />
    </button>
    <button
      v-for="asset in available"
      :key="asset.id"
      type="button"
      :disabled="state.busy || (atLimit && !selected.includes(asset.id))"
      :class="['attachment-chip', { selected: selected.includes(asset.id) }]"
      :aria-pressed="selected.includes(asset.id)"
      :title="
        atLimit && !selected.includes(asset.id)
          ? '本条最多引用 6 份；资料已保存在项目中，取消一份后可选择'
          : asset.name
      "
      @click="toggle(asset.id)"
    >
      {{ asset.media_type.startsWith('image/') ? '▧' : '≡' }} {{ asset.name }}
    </button>
    <small
      v-if="selected.length"
      class="attachment-count"
      :title="`${selected.length}/${LIMIT} 份资料内容将在发送时提供给当前模型${atLimit ? '，已满' : ''}`"
      >{{ selected.length }}/{{ LIMIT }}</small
    >
    <div
      v-if="uploading || report || formatError"
      class="attachment-upload-status"
      role="status"
      tabindex="0"
      aria-label="资料上传状态"
    >
      <p v-if="uploading">
        {{
          attachmentTransfer?.phase === 'preparing'
            ? '正在读取资料格式，文件尚未上传…'
            : attachmentTransfer?.phase === 'refreshing'
              ? '正在同步已处理的资料…'
              : `正在处理资料（已确认 ${attachmentTransfer?.completed}/${attachmentTransfer?.total} 个文件）…`
        }}
      </p>
      <template v-if="report">
        <p v-if="report.assets.length">
          本次已保存/复用 {{ report.assets.length }} 份资料；其中当前已引用 {{ referenced }} 份，{{
            report.assets.length - referenced
          }}
          份未引用<span v-if="report.assets.length > referenced"
            >（本条最多 {{ LIMIT }} 份，可稍后选择）</span
          >
        </p>
        <p v-if="report.failure">
          {{ report.failure.status === 'not_uploaded' ? '未上传' : '未确认保存' }}「{{
            report.failure.name
          }}」：{{ report.failure.message }}。<span v-if="report.unattempted.length"
            >其余 {{ report.unattempted.length }} 个文件尚未尝试。</span
          >
        </p>
        <p v-if="report.failure || report.selectionError">
          请先检查项目资料，再重新选择未确认或尚未尝试的文件；重复文件会复用已有资料。
        </p>
        <details v-if="report.unattempted.length" class="attachment-unattempted">
          <summary>查看尚未尝试的文件（{{ report.unattempted.length }}）</summary>
          <ul>
            <li v-for="(name, index) in report.unattempted" :key="index">{{ name }}</li>
          </ul>
        </details>
        <p v-if="report.selectionError">{{ report.selectionError }}</p>
        <p v-if="report.refresh === 'failed' || report.refresh === 'timeout'">
          项目同步暂未完成；已确认保存的资料仍可引用。
        </p>
      </template>
      <p v-if="formatError">
        {{ formatError }}
        <button type="button" class="text-button" @click="loadFormats">重试读取格式</button>
      </p>
    </div>
  </div>
</template>

<style scoped>
.attachment-picker {
  max-height: min(180px, 24vh);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.attachment-upload-help,
.attachment-upload-status {
  flex-basis: 100%;
  font-size: 11px;
  color: var(--text-secondary);
  line-height: 1.5;
}
.attachment-unattempted li {
  overflow-wrap: anywhere;
}
.attachment-upload-status p {
  margin: 3px 0;
  overflow-wrap: anywhere;
}
.attachment-upload-status {
  max-height: 112px;
  overflow-y: auto;
}
</style>
