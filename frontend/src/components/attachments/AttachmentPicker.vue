<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { LoaderCircle, Plus, X, FilePlus2, Files, Settings2 } from 'lucide-vue-next';
import { useAttachments } from '../../composables/useAttachments';
import { useWorkspace } from '../../composables/useWorkspace';
import { useNotifications } from '../../composables/useNotifications';
import { agentDrafts, useAgentDraft } from '../../composables/useAgentDrafts';
import type { Project } from '../../types';

const props = defineProps<{ project: Project; contextKey?: string; inlineIds?: string[] }>();
const emit = defineEmits<{ settings: [] }>();
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
const selectedAssets = computed(() =>
  available.value.filter((asset) => selected.value.includes(asset.id)),
);
const menuOpen = ref(false);
const savedOpen = ref(false);
const toolsAnchor = ref<HTMLElement>();
const addButton = ref<HTMLButtonElement>();
const toolsPanel = ref<HTMLElement>();
let menuSequence = 0;
let disposed = false;
let menuAnimation: Animation | undefined;
const motionPreference =
  typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
function cancelMenuMotion() {
  menuAnimation?.cancel();
  menuAnimation = undefined;
}
function motionChanged(event: MediaQueryListEvent) {
  if (event.matches) cancelMenuMotion();
}
async function closeMenu(restoreFocus = false) {
  const sequence = ++menuSequence;
  const previousFocus = typeof document !== 'undefined' ? document.activeElement : null;
  cancelMenuMotion();
  menuOpen.value = false;
  savedOpen.value = false;
  if (restoreFocus) {
    await nextTick();
    if (disposed || sequence !== menuSequence || menuOpen.value) return;
    // A newer pointer/focus action outside the menu owns focus, even before
    // this close's nextTick completes.
    if (
      document.activeElement === previousFocus ||
      document.activeElement === document.body ||
      toolsAnchor.value?.contains(document.activeElement)
    )
      addButton.value?.focus();
  }
}
async function toggleMenu(event?: MouseEvent) {
  if (menuOpen.value) {
    await closeMenu(true);
    return;
  }
  const sequence = ++menuSequence;
  cancelMenuMotion();
  menuOpen.value = true;
  await nextTick();
  if (disposed || sequence !== menuSequence || !menuOpen.value) return;
  toolsPanel.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  // Occasional pointer disclosure shows where the tools came from. Keyboard,
  // assistive activation and coarse pointers keep immediate final content.
  if (
    event?.detail &&
    typeof matchMedia === 'function' &&
    matchMedia('(hover: hover) and (pointer: fine)').matches &&
    !motionPreference?.matches
  ) {
    menuAnimation = toolsPanel.value?.animate?.(
      [
        { opacity: 0, transform: 'scale(0.97)' },
        { opacity: 1, transform: 'scale(1)' },
      ],
      { duration: 180, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' },
    );
  }
}
function outside(event: Event) {
  if (menuOpen.value && event.target instanceof Node && !toolsAnchor.value?.contains(event.target))
    void closeMenu();
}
function focusLeft(event: FocusEvent) {
  if (event.relatedTarget instanceof Node && !toolsAnchor.value?.contains(event.relatedTarget))
    void closeMenu();
}
function uploadFromMenu() {
  input.value?.click();
  void closeMenu(true);
}
function settingsFromMenu() {
  void closeMenu();
  emit('settings');
}
watch(
  () => [props.project.id, props.contextKey],
  () => void closeMenu(),
);
onMounted(() => {
  motionPreference?.addEventListener?.('change', motionChanged);
  if (typeof document !== 'undefined') document.addEventListener?.('pointerdown', outside);
});
onUnmounted(() => {
  disposed = true;
  menuSequence++;
  cancelMenuMotion();
  motionPreference?.removeEventListener?.('change', motionChanged);
  if (typeof document !== 'undefined') document.removeEventListener?.('pointerdown', outside);
});
const input = ref<HTMLInputElement>();
const LIMIT = 6;
const referencedIds = computed(() => [...new Set([...selected.value, ...(props.inlineIds ?? [])])]);
const atLimit = computed(() => referencedIds.value.length >= LIMIT);
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

defineExpose({ acceptFiles, closeMenu });
function toggle(id: string) {
  if (selected.value.includes(id)) {
    selected.value = selected.value.filter((x) => x !== id);
    return;
  }
  if (atLimit.value && !props.inlineIds?.includes(id)) return;
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
    <div v-if="selectedAssets.length" class="attachment-selection" aria-label="本条引用的项目资料">
      <button
        v-for="asset in selectedAssets"
        :key="asset.id"
        type="button"
        class="attachment-chip selected"
        :disabled="state.busy"
        :aria-label="`取消引用 ${asset.name}`"
        :title="asset.name"
        @click="toggle(asset.id)"
      >
        <span class="attachment-chip-name"
          >{{ asset.media_type.startsWith('image/') ? '▧' : '≡' }} {{ asset.name }}</span
        ><X :size="12" aria-hidden="true" />
      </button>
    </div>
    <div
      ref="toolsAnchor"
      class="attachment-tools-anchor"
      @focusout="focusLeft"
      @keydown.esc.stop.prevent="closeMenu(true)"
    >
      <button
        ref="addButton"
        type="button"
        class="attachment-add"
        aria-label="添加资料与工具"
        title="添加资料与工具"
        aria-haspopup="dialog"
        :aria-expanded="menuOpen"
        @click="toggleMenu"
      >
        <LoaderCircle v-if="uploading" :size="17" class="spinning" aria-hidden="true" /><Plus
          v-else
          :size="23"
          aria-hidden="true"
        />
      </button>
      <div
        v-if="menuOpen"
        ref="toolsPanel"
        class="attachment-tools"
        role="dialog"
        aria-label="添加资料与工具"
      >
        <button
          type="button"
          class="attachment-tool"
          :disabled="state.busy || uploading"
          @click="uploadFromMenu"
        >
          <FilePlus2 :size="16" aria-hidden="true" />添加文件或图片
        </button>
        <button
          type="button"
          class="attachment-tool"
          :aria-expanded="savedOpen"
          @click="savedOpen = !savedOpen"
        >
          <Files :size="16" aria-hidden="true" />引用项目资料<small
            >{{ referencedIds.length }}/{{ LIMIT }}</small
          >
        </button>
        <div v-if="savedOpen" class="attachment-saved-list">
          <p v-if="!available.length">项目中还没有资料，可以先添加文件。</p>
          <button
            v-for="asset in available"
            :key="asset.id"
            type="button"
            class="attachment-tool"
            :disabled="
              state.busy ||
              (atLimit && !selected.includes(asset.id) && !inlineIds?.includes(asset.id))
            "
            :aria-pressed="selected.includes(asset.id)"
            :title="
              atLimit && !selected.includes(asset.id) && !inlineIds?.includes(asset.id)
                ? '本条最多引用6份；资料已保存在项目中，取消一份后可选择'
                : asset.name
            "
            @click="toggle(asset.id)"
          >
            <span>{{ asset.name }}</span
            ><small v-if="selected.includes(asset.id)">已引用</small>
          </button>
        </div>
        <button type="button" class="attachment-tool" @click="settingsFromMenu">
          <Settings2 :size="16" aria-hidden="true" />模型与工具设置
        </button>
        <small class="attachment-upload-help"
          >所选文件上传成功后都会保存到项目；本条消息最多引用 {{ LIMIT }} 份资料内容</small
        >
        <p v-if="formatError" class="attachment-format-error" role="status">
          {{ formatError
          }}<button type="button" class="text-button" @click="loadFormats">重试读取格式</button>
        </p>
      </div>
    </div>
  </div>
</template>
