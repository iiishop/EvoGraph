<script setup lang="ts">
import { computed } from 'vue';
import { FlaskConical, RefreshCw } from 'lucide-vue-next';
import type { Project } from '../../types';
import { candidateStatusLabel } from '../../lib/planPreview';
import PlanReviewDisclosure from './PlanReviewDisclosure.vue';
import { useWorkspace } from '../../composables/useWorkspace';
const props = defineProps<{ project: Project; preview: boolean }>();
const emit = defineEmits<{ preview: [value: boolean] }>();
const { state, perform } = useWorkspace();
const candidate = computed(() => props.project.plan_candidate);
async function toggle() {
  if (state.busy) return;
  await perform('plan.unified_enable', {
    project_id: props.project.id,
    enabled: !props.project.unified_planning,
  });
}
</script>
<template>
  <div class="unified-plan-bar">
    <button
      type="button"
      class="unified-plan-toggle"
      :aria-pressed="Boolean(project.unified_planning)"
      :disabled="state.busy"
      title="仅对当前项目启用实验：先预览候选，完成检查后自动更新正式方案"
      @click="toggle"
    >
      <FlaskConical :size="13" aria-hidden="true" />统一规划
      <span>{{ project.unified_planning ? '实验已开启' : '实验未开启' }}</span>
    </button>
    <div v-if="candidate" class="plan-version-switch" role="group" aria-label="查看方案版本">
      <button type="button" :aria-pressed="!preview" @click="emit('preview', false)">
        正式方案 <span>R{{ project.revision }}</span>
      </button>
      <button type="button" :aria-pressed="preview" @click="emit('preview', true)">
        候选预览 <span>{{ candidateStatusLabel(candidate) }}</span>
      </button>
    </div>
    <p v-else-if="project.unified_planning" class="plan-mode-hint">
      <RefreshCw v-if="state.busy" :size="12" class="spinning" aria-hidden="true" />
      {{ state.busy ? '准备候选，正式方案保留' : '下次规划会先展示候选，评审接受后自动提交' }}
    </p>
    <PlanReviewDisclosure
      v-if="!preview"
      :candidate="candidate"
      :canonical-revision="project.revision"
      align="end"
      class="plan-canonical-review"
    />
  </div>
</template>
<style scoped>
.unified-plan-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 38px;
  padding: 4px 20px;
  border-bottom: 1px solid var(--line);
  background: var(--canvas, #f6f8fb);
  flex-wrap: wrap;
}
.unified-plan-toggle,
.plan-version-switch button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  font-size: 11px;
  padding: 5px 7px;
  border-radius: 5px;
}
.unified-plan-toggle[aria-pressed='true'] {
  color: var(--accent);
}
.unified-plan-toggle span {
  font-size: 10px;
  color: var(--text-secondary);
}
.plan-version-switch {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: white;
}
.plan-version-switch button[aria-pressed='true'] {
  background: #edf2f9;
  color: var(--accent);
}
.plan-version-switch span,
.plan-mode-hint {
  font-size: 10px;
  color: var(--text-secondary);
}
.plan-mode-hint {
  display: flex;
  gap: 5px;
  align-items: center;
  margin: 0;
}
.plan-canonical-review {
  margin-left: auto;
}
@media (max-width: 760px) {
  .unified-plan-bar {
    padding-inline: 12px;
    gap: 6px;
  }
  .plan-canonical-review {
    margin-left: 0;
  }
}
</style>
