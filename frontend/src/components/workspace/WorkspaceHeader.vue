<script setup lang="ts">
import { ChevronRight, GitBranch, SlidersHorizontal, RefreshCw, Folder } from 'lucide-vue-next';
import type { Project } from '../../types';
import { useWorkspace } from '../../composables/useWorkspace';
import { useBaseline } from '../../composables/useBaseline';
defineProps<{ project: Project }>();
defineEmits<{ edit: [] }>();
const { state } = useWorkspace();
const baseline = useBaseline();
</script>
<template>
  <header class="workspace-header">
    <div class="breadcrumb">
      <Folder :size="14" /> 项目 <ChevronRight :size="13" /><span>{{ project.name }}</span
      ><span v-if="project.is_demo" class="demo-tag">示例 · 未执行</span
      ><span class="header-local"><span class="live-dot"></span> SQLite 本地存储</span>
    </div>
    <div class="title-row">
      <div>
        <h1>{{ project.name }}<span class="title-tag">演化工作台</span></h1>
        <p>
          {{ project.description || '让每一步演化，都有明确的目标和依据。' }}
        </p>
      </div>
      <div class="header-actions">
        <button
          class="button secondary"
          :disabled="state.busy || !project.repository"
          :title="project.repository ? '扫描仓库并更新基线' : '请先在项目设置中连接仓库'"
          @click="baseline.refresh(project)"
        >
          <RefreshCw :size="15" :class="{ spinning: state.busy }" />
          刷新基线</button
        ><button class="icon-button bordered" aria-label="项目设置" @click="$emit('edit')">
          <SlidersHorizontal :size="17" />
        </button>
      </div>
    </div>
    <div class="version-strip">
      <span
        ><span class="version-letter target">T</span> 目标
        <strong>V{{ project.targets.at(-1)?.number ?? 0 }}</strong></span
      ><i></i
      ><span
        ><GitBranch :size="14" /> 规划
        <strong>P{{ project.plans.at(-1)?.number ?? 0 }}</strong></span
      ><i></i
      ><span
        ><span class="version-letter baseline">B</span> 基线
        <strong>{{
          project.baselines.length ? `B${project.baselines.at(-1)?.number}` : '尚未连接'
        }}</strong></span
      ><span class="target-progress"
        ><span class="progress-track"
          ><span
            :style="{
              width: `${project.acceptance.total ? (project.acceptance.passed / project.acceptance.total) * 100 : 0}%`,
            }"
          ></span></span
        ><strong>{{ project.acceptance.passed }} / {{ project.acceptance.total }}</strong>
        行为已验证</span
      >
    </div>
  </header>
</template>
