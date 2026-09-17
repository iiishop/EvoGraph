<script setup lang="ts">
import { Flag } from 'lucide-vue-next';
import type { Project } from '../../types';
defineProps<{ project: Project }>();
</script>
<template>
  <div class="goal-marker" :class="{ achieved: project.acceptance.achieved }">
    <Flag :size="15" />
    <div>
      <small>目标 · {{ project.acceptance.achieved ? '已达成' : '待达成' }} · 非 PR 节点</small
      ><strong>{{
        project.targets.at(-1)?.statement || project.description || project.name
      }}</strong>
    </div>
    <span>{{ project.acceptance.passed }}/{{ project.acceptance.total }}</span>
  </div>
</template>
