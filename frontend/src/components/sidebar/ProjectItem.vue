<script setup lang="ts">
import { computed } from 'vue';
import { GitBranch, Trash2 } from 'lucide-vue-next';
import { useWorkspace } from '../../composables/useWorkspace';
import type { ProjectSummary } from '../../types';

const props = defineProps<{ project: ProjectSummary; active: boolean; pinned?: boolean }>();
defineEmits<{ select: [] }>();
const { state, deleteProject } = useWorkspace();

const progress = computed(
  () => props.project.acceptance ?? { passed: 0, total: 0, achieved: false },
);
const percent = computed(() =>
  progress.value.total ? Math.round((progress.value.passed / progress.value.total) * 100) : 0,
);
// 这一行说的是这个产品的语言：通过验收的行为数，而不是通用的"里程碑数量"
const meta = computed(() => {
  const { passed, total } = progress.value;
  if (props.pinned) return '当前项目 · 已被筛选隐藏';
  if (!total) return '尚无验收目标';
  return props.project.is_demo ? `示例 · ${passed}/${total}` : `${passed}/${total} 行为已验证`;
});
const barTitle = computed(() => percent.value + '% 的行为已通过验证');
</script>

<template>
  <div class="project-item" :class="{ active, pinned }">
    <button
      class="project-select"
      :aria-current="active ? 'page' : undefined"
      @click="$emit('select')"
    >
      <span class="project-icon">
        <GitBranch :size="15" aria-hidden="true" />
      </span>
      <span class="project-name" :title="project.name">
        {{ project.name }}
        <small>{{ meta }}</small>
      </span>
    </button>
    <span class="project-bar" :class="{ achieved: progress.achieved }" :title="barTitle">
      <i :style="{ transform: `scaleX(${percent / 100})` }" />
    </span>
    <button
      class="icon-button project-delete"
      :aria-label="`删除项目 ${project.name}`"
      :title="`删除项目「${project.name}」`"
      :disabled="state.busy"
      @click="deleteProject(project)"
    >
      <Trash2 :size="14" aria-hidden="true" />
    </button>
  </div>
</template>
