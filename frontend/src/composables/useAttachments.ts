import { ref } from 'vue';
import { command } from '../api/client';
import { useWorkspace } from './useWorkspace';
import {
  attachmentRead,
  uploadAttachmentBatch,
  type AttachmentUploadHooks,
} from '../lib/attachmentUpload';
import type { Attachment } from '../types';

export function useAttachments() {
  const uploading = ref(false);
  const formats = ref({ extensions: [] as string[], max_bytes: 8 * 1024 * 1024 });
  const formatError = ref('');
  let pendingFormats: Promise<boolean> | undefined;
  function loadFormats(): Promise<boolean> {
    if (pendingFormats) return pendingFormats;
    pendingFormats = (async () => {
      formatError.value = '';
      try {
        const result = await attachmentRead(command<typeof formats.value>('attachments.formats'));
        if (
          !Array.isArray(result.extensions) ||
          !Number.isFinite(result.max_bytes) ||
          result.max_bytes <= 0
        )
          throw new Error('资料格式信息不完整');
        formats.value = result;
        return true;
      } catch {
        formatError.value = '支持格式暂时读取失败，请重试后选择文件';
        return false;
      } finally {
        pendingFormats = undefined;
      }
    })();
    return pendingFormats;
  }
  async function uploadBatch(
    projectId: string,
    files: FileList | File[],
    hooks: AttachmentUploadHooks = {},
  ) {
    const workspace = useWorkspace();
    if (workspace.state.busy || uploading.value) return;
    uploading.value = true;
    workspace.setBusy(true);
    try {
      return await uploadAttachmentBatch(Array.from(files), {
        ...hooks,
        maxBytes: formats.value.max_bytes,
        read: (file) =>
          new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(',')[1]);
            reader.onerror = () => reject(new Error('读取文件失败'));
            reader.onabort = () => reject(new Error('读取文件已取消'));
            reader.readAsDataURL(file);
          }),
        save: (name, content) =>
          command<Attachment>('attachments.upload', { project_id: projectId, name, content }),
        refresh: () => workspace.refresh(),
      });
    } finally {
      // Receipts, reference selection and the bounded refresh all settle before
      // releasing this operation's busy ownership.
      uploading.value = false;
      workspace.setBusy(false);
    }
  }
  // Keep the prior ID-array API available to non-picker callers.
  async function upload(projectId: string, files: FileList | File[]) {
    const result = await uploadBatch(projectId, files);
    const workspace = useWorkspace();
    if (result?.failure && workspace.state.project?.id === projectId)
      workspace.setError(
        `${result.failure.name}：${result.failure.message}；已确认保存的资料会保留`,
      );
    return result?.assets.map((asset) => asset.id) ?? [];
  }
  return { uploading, formats, formatError, loadFormats, upload, uploadBatch };
}
