<script setup lang="ts">
import { GitBranch, Trash2 } from 'lucide-vue-next';
import { useWorkspace } from '../../composables/useWorkspace';
import type { ProjectSummary } from '../../types';
defineProps<{ project: ProjectSummary; active: boolean }>();
defineEmits<{ select: [] }>();
const { state, deleteProject } = useWorkspace();
</script>
<template>
  <div class="project-item" :class="{ active }">
    <button class="project-select" @click="$emit('select')">
      <span class="project-icon"><GitBranch :size="16" /></span
      ><span class="project-name"
        >{{ project.name
        }}<small>{{
          project.is_demo ? '示例项目' : `${project.milestone_count} 个里程碑`
        }}</small></span
      >
    </button>
    <button
      class="icon-button project-delete"
      :aria-label="`删除项目 ${project.name}`"
      :disabled="state.busy"
      @click="deleteProject(project)"
    >
      <Trash2 :size="14" />
    </button>
  </div>
</template>
