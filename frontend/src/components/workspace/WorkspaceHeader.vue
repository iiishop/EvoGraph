<script setup lang="ts">
import { computed } from 'vue';
import {
  ChevronRight,
  GitBranch,
  SlidersHorizontal,
  RefreshCw,
  Folder,
  FolderOpen,
} from 'lucide-vue-next';
import type { Project } from '../../types';
import { acceptanceSummary } from '../../lib/acceptance';
import { useWorkspace } from '../../composables/useWorkspace';
import { useBaseline } from '../../composables/useBaseline';
const props = defineProps<{ project: Project }>();
defineEmits<{ edit: [] }>();
const { state } = useWorkspace();
const baseline = useBaseline();
const latestBaseline = computed(() => props.project.baselines.at(-1));
const acceptance = computed(() => acceptanceSummary(props.project));
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
        <div
          class="repository-location"
          :title="project.repository || '可在项目设置中连接本地仓库'"
        >
          <FolderOpen :size="13" />
          <span>{{ project.repository || '尚未连接本地仓库，可先规划项目' }}</span>
          <small v-if="project.repository && !latestBaseline">已关联 · 等待读取基线</small>
        </div>
        <p
          v-if="project.repository && !latestBaseline && state.settings?.provider"
          class="source-analysis-note"
        >
          首次读取后，会将源码上下文发送给已配置的模型，分析已实现能力。
        </p>
      </div>
      <div class="header-actions">
        <button
          class="button secondary"
          :disabled="state.busy"
          :title="
            project.repository
              ? state.settings?.provider
                ? '读取仓库基线，按需将源码上下文发送给已配置模型分析'
                : '只读扫描仓库文件；配置模型后可继续分析源码'
              : '打开项目设置，连接本地仓库'
          "
          @click="project.repository ? baseline.refresh(project) : $emit('edit')"
        >
          <RefreshCw v-if="project.repository" :size="15" :class="{ spinning: state.busy }" />
          <FolderOpen v-else :size="15" />
          {{ !project.repository ? '连接仓库' : latestBaseline ? '刷新基线' : '读取基线' }}</button
        ><button class="icon-button bordered" aria-label="项目设置" @click="$emit('edit')">
          <SlidersHorizontal :size="17" />
        </button>
      </div>
    </div>
    <div class="version-strip">
      <span
        ><span class="version-letter target">T</span> 最终目标
        <strong>V{{ project.targets.at(-1)?.number ?? 0 }}</strong></span
      ><i></i
      ><span
        ><GitBranch :size="14" /> 规划
        <strong>P{{ project.plans.at(-1)?.number ?? 0 }}</strong></span
      ><i></i
      ><span
        ><span class="version-letter baseline">B</span> 基线
        <strong>{{
          latestBaseline
            ? `B${latestBaseline.number}${latestBaseline.complete ? '' : ' · 扫描不完整'}`
            : project.repository
              ? '尚未读取'
              : '未连接仓库'
        }}</strong></span
      ><span class="target-progress"
        ><span class="progress-track"
          ><span
            :style="{
              width: `${acceptance.total ? (acceptance.passed / acceptance.total) * 100 : 0}%`,
            }"
          ></span></span
        ><strong>{{ acceptance.passed }} / {{ acceptance.total }}</strong>
        {{ acceptance.total ? '最终目标已验证' : '尚未定义最终目标标准' }}</span
      >
    </div>
  </header>
</template>
<style scoped>
.title-row > div:first-child {
  min-width: 0;
}
.header-actions {
  flex-shrink: 0;
}
.repository-location {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 9px;
  color: #7d8d82;
  font-size: 10px;
}
.repository-location > svg,
.repository-location > small {
  flex-shrink: 0;
}
.title-row p.source-analysis-note {
  margin-top: 6px;
  font-size: 10px;
  line-height: 1.5;
  color: #7d8d82;
}
.repository-location > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.repository-location > small {
  font-size: 10px;
  color: #8a805c;
}
@media (max-width: 760px) {
  .repository-location {
    flex-wrap: wrap;
  }
  .repository-location > span {
    max-width: calc(100% - 20px);
  }
}
</style>
