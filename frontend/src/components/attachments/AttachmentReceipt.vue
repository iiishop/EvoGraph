<script setup lang="ts">
import { computed } from 'vue';
import type { AttachmentUploadResult } from '../../lib/attachmentUpload';
const props = defineProps<{
  transfer: {
    total: number;
    completed: number;
    phase: 'preparing' | 'uploading' | 'refreshing' | 'done';
    report: AttachmentUploadResult | null;
  } | null;
  selectedIds: string[];
}>();
const report = computed(() => props.transfer?.report);
const referenced = computed(
  () => report.value?.assets.filter((asset) => props.selectedIds.includes(asset.id)).length ?? 0,
);
</script>
<template>
  <section v-if="transfer" class="attachment-receipt" role="status" aria-label="资料上传状态">
    <strong>资料保存</strong>
    <p v-if="transfer.phase !== 'done'">
      {{
        transfer.phase === 'preparing'
          ? '正在读取资料格式，文件尚未上传…'
          : transfer.phase === 'refreshing'
            ? '正在同步已处理的资料…'
            : `正在处理资料（已确认 ${transfer.completed}/${transfer.total} 个文件）…`
      }}
    </p>
    <template v-if="report">
      <p v-if="report.assets.length">
        本次已保存/复用 {{ report.assets.length }} 份资料；其中当前已引用 {{ referenced }} 份，{{
          report.assets.length - referenced
        }}
        份未引用<span v-if="report.assets.length > referenced">（本条最多 6 份，可稍后选择）</span>
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
      <details v-if="report.unattempted.length">
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
  </section>
</template>
