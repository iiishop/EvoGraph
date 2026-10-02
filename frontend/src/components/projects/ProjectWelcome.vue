<script setup lang="ts">
import { ArrowRight, Orbit, GitBranch, Layers3 } from 'lucide-vue-next';
defineProps<{ reconnect?: boolean }>();
defineEmits<{ create: []; retry: [] }>();
</script>
<template>
  <section class="project-welcome" aria-labelledby="project-welcome-title">
    <header class="project-welcome-brand">
      <Orbit :size="25" /><span>EvoGraph</span><small>本地工作空间</small>
    </header>
    <div class="project-welcome-body">
      <div class="project-welcome-copy">
        <span class="project-entry-eyebrow">从想法到可交付的一步</span>
        <h1 id="project-welcome-title">给一个目标，<br />让路线逐步清晰。</h1>
        <p>创建项目后描述目标，或连接已有仓库。<br />规划、架构与对话会在同一个空间演化。</p>
        <button class="button primary" @click="reconnect ? $emit('retry') : $emit('create')">
          {{ reconnect ? '重新连接后端' : '创建新项目' }}<ArrowRight :size="16" />
        </button>
        <small>项目留在本地 · 没有仓库也可以先规划</small>
      </div>
      <div class="project-welcome-preview" aria-label="项目中的规划方式">
        <GitBranch :size="24" />
        <h2>把大目标，拆成能交付的小步</h2>
        <p>先说清想做什么，再补充约束、调整方向。里程碑和依赖关系会随规划逐步清晰。</p>
        <div><Layers3 :size="18" /><span>需要架构时，在同一处继续讨论设计</span></div>
        <div><Orbit :size="18" /><span>计划与验收证据分开保留，结果可追溯</span></div>
      </div>
    </div>
  </section>
</template>
