<script setup lang="ts">
import { computed } from 'vue';
import type { Project } from '../../types';
import { useBaseline } from '../../composables/useBaseline';
import { useWorkspace } from '../../composables/useWorkspace';
const props = defineProps<{ project: Project }>();
const { state } = useWorkspace();
const baseline = useBaseline();
const current = computed(() => props.project.baselines.at(-1));
const fresh = computed(() => props.project.source_analysis_baseline_id === current.value?.id);
</script>
<template>
  <div v-if="current" class="baseline-milestone-status" role="status">
    <span class="source-badge">SRC</span>
    <span :title="project.source_analysis_summary">
      {{
        !current.complete
          ? '基线扫描不完整，请刷新'
          : fresh
            ? `基线已实现 · ${project.source_milestones.length} 个里程碑`
            : project.source_milestones.length
              ? '基线已变化 · 已实现里程碑待更新'
              : '当前基线 · 待倒推已实现里程碑'
      }}
    </span>
    <button
      class="button secondary"
      :disabled="state.busy || !current.complete"
      @click="baseline.reconstruct(project)"
    >
      {{ fresh ? '重新倒推' : '倒推已实现里程碑' }}
    </button>
  </div>
</template>
<style scoped>
.baseline-milestone-status {
  position: absolute;
  z-index: 5;
  bottom: 16px;
  left: 64px;
  right: 160px;
  width: fit-content;
  max-width: calc(100% - 224px);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 8px 12px;
  background: #ffffffed;
  border: 1px solid var(--line, #d6dfe4);
  border-radius: 10px;
  font-size: 12px;
  color: var(--muted, #607480);
}
.baseline-milestone-status .button {
  font-size: 12px;
  padding: 5px 8px;
  min-height: 28px;
}
</style>
