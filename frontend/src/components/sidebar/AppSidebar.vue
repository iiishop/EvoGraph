<script setup lang="ts">
import { Plus, ArrowUpRight, Orbit, PanelLeftClose } from 'lucide-vue-next';
import ProjectItem from './ProjectItem.vue';
import SidebarNavItem from './SidebarNavItem.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import { navigation } from '../../lib/navigation';
const { state, selectProject, setPage } = useWorkspace();
defineEmits<{ create: [] }>();
</script>
<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-mark"><Orbit :size="25" /></span
      ><strong>EvoGraph<span>PROJECT EVOLUTION</span></strong
      ><PanelLeftClose :size="17" class="muted sidebar-decoration" />
    </div>
    <div class="workspace-label">工作空间 <span>LOCAL</span></div>
    <nav aria-label="主导航">
      <SidebarNavItem
        v-for="item in navigation"
        :key="item.id"
        :icon="item.icon"
        :label="item.label"
        :count="item.countProjects ? state.projects.length : undefined"
        :active="state.page === item.id"
        @select="setPage(item.id)"
      />
    </nav>
    <div class="section-label">
      我的项目<button class="icon-button small" aria-label="新建项目" @click="$emit('create')">
        <Plus :size="16" />
      </button>
    </div>
    <div class="project-list">
      <ProjectItem
        v-for="project in state.projects"
        :key="project.id"
        :project="project"
        :active="state.project?.id === project.id && state.page === 'projects'"
        @select="selectProject(project.id)"
      />
    </div>
    <button class="add-project" @click="$emit('create')"><Plus :size="15" /> 新建项目</button>
    <div class="sidebar-bottom">
      <div class="local-card">
        <span class="live-dot"></span>
        <div>本地优先，持续演化<small>项目与证据保存在此设备</small></div>
        <ArrowUpRight :size="15" />
      </div>
      <div class="sidebar-footer">
        <span class="avatar">E</span>
        <div>个人工作空间<small>EvoGraph v0.1.0</small></div>
        <span class="local-tag">本地</span>
      </div>
    </div>
  </aside>
</template>
