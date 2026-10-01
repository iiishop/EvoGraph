import type { Attachment } from '../types';

export class AttachmentReadTimeout extends Error {}
export async function attachmentRead<T>(read: Promise<T>, timeoutMs = 3000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      read,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new AttachmentReadTimeout('读取资料信息超时')), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export interface AttachmentUploadResult {
  assets: Attachment[];
  confirmedFiles: string[];
  failure?: { name: string; message: string; status: 'not_uploaded' | 'unconfirmed' };
  selectionError?: string;
  unattempted: string[];
  refresh: 'not_needed' | 'complete' | 'failed' | 'timeout';
}
export interface AttachmentUploadHooks {
  onConfirmed?: (asset: Attachment) => void;
  onPhase?: (phase: 'uploading' | 'refreshing', completed: number) => void;
  isCurrent?: () => boolean;
  onComplete?: (result: AttachmentUploadResult) => void;
}

function receipt(value: Attachment): Attachment {
  if (
    !value ||
    typeof value.id !== 'string' ||
    !value.id ||
    typeof value.name !== 'string' ||
    typeof value.media_type !== 'string' ||
    !Number.isFinite(value.size)
  )
    throw new Error('未收到完整的资料保存结果');
  // The receipt cache only needs metadata. Do not duplicate extracted document
  // contents in the session's composer state.
  return {
    id: value.id,
    name: value.name,
    media_type: value.media_type,
    size: value.size,
    excerpt: '',
  };
}

export async function uploadAttachmentBatch(
  files: File[],
  options: AttachmentUploadHooks & {
    maxBytes: number;
    read: (file: File) => Promise<string>;
    save: (name: string, content: string) => Promise<Attachment>;
    refresh: () => Promise<void>;
    refreshTimeoutMs?: number;
  },
): Promise<AttachmentUploadResult> {
  const result: AttachmentUploadResult = {
    assets: [],
    confirmedFiles: [],
    unattempted: [],
    refresh: 'not_needed',
  };
  const assets = new Map<string, Attachment>();
  let attemptedSave = false;
  for (const [index, file] of files.entries()) {
    if (options.isCurrent?.() === false) {
      result.unattempted = files.slice(index).map((item) => item.name);
      break;
    }
    options.onPhase?.('uploading', result.confirmedFiles.length);
    let content: string;
    try {
      if (file.size > options.maxBytes) throw new Error('文件超过当前允许的大小上限');
      content = await options.read(file);
    } catch (error) {
      result.failure = {
        name: file.name,
        status: 'not_uploaded',
        message: error instanceof Error ? error.message : '读取文件失败',
      };
      result.unattempted = files.slice(index + 1).map((item) => item.name);
      break;
    }
    if (options.isCurrent?.() === false) {
      result.unattempted = files.slice(index).map((item) => item.name);
      break;
    }
    try {
      attemptedSave = true;
      // A mutation is not converted into a failed upload by a read deadline.
      // Only an actual receipt can confirm that this asset was saved/reused.
      const asset = receipt(await options.save(file.name, content));
      assets.set(asset.id, asset);
      result.confirmedFiles.push(file.name);
      try {
        options.onConfirmed?.(asset);
      } catch {
        result.selectionError = '资料已确认保存，但自动引用未完成，请检查后手动选择。';
      }
    } catch (error) {
      result.failure = {
        name: file.name,
        status: 'unconfirmed',
        message: error instanceof Error ? error.message : '没有收到资料保存确认',
      };
      result.unattempted = files.slice(index + 1).map((item) => item.name);
      break;
    }
  }
  result.assets = [...assets.values()];
  if (attemptedSave && options.isCurrent?.() !== false) {
    options.onPhase?.('refreshing', result.confirmedFiles.length);
    try {
      await attachmentRead(options.refresh(), options.refreshTimeoutMs);
      result.refresh = 'complete';
    } catch (error) {
      result.refresh = error instanceof AttachmentReadTimeout ? 'timeout' : 'failed';
    }
  }
  options.onComplete?.(result);
  return result;
}
