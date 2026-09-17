import { ref } from 'vue';
import { command } from '../api/client';
import { useWorkspace } from './useWorkspace';
import type { Attachment } from '../types';

export function useAttachments() {
  const uploading = ref(false);
  const formats = ref({ extensions: [] as string[], max_bytes: 8 * 1024 * 1024 });
  async function loadFormats() {
    formats.value = await command('attachments.formats');
  }
  async function upload(projectId: string, files: FileList | File[]) {
    const workspace = useWorkspace();
    const added: string[] = [];
    uploading.value = true;
    workspace.setBusy(true);
    try {
      for (const file of Array.from(files)) {
        if (file.size > formats.value.max_bytes) throw new Error(`${file.name} 超过 8 MB`);
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = () => reject(new Error('读取文件失败'));
          reader.readAsDataURL(file);
        });
        const asset = await command<Attachment>('attachments.upload', {
          project_id: projectId,
          name: file.name,
          content: data,
        });
        added.push(asset.id);
      }
    } catch (error) {
      workspace.setError(String(error));
    } finally {
      uploading.value = false;
      workspace.setBusy(false);
      await workspace.refresh();
    }
    return added;
  }
  return { uploading, formats, loadFormats, upload };
}
