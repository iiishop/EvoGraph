<script setup lang="ts">
import { Plus, GitBranch, HardDrive } from 'lucide-vue-next';
import { ref, computed } from 'vue';
import ProjectItem from './ProjectItem.vue';
import SidebarNavItem from './SidebarNavItem.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import { navigation } from '../../lib/navigation';
const { state, selectProject, setPage } = useWorkspace();
const query = ref('');
const projects = computed(() =>
  state.projects.filter((p) => p.name.toLowerCase().includes(query.value.toLowerCase())),
);
defineEmits<{ create: [] }>();
</script>
<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-mark"><GitBranch :size="25" /></span
      ><strong>EvoGraph<span>项目演化工作台</span></strong>
    </div>
    <div class="workspace-label">工作空间</div>
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
    <input v-model="query" class="project-search" aria-label="筛选项目" placeholder="筛选项目…" />
    <div class="project-list">
      <ProjectItem
        v-for="project in projects"
        :key="project.id"
        :project="project"
        :active="state.project?.id === project.id && state.page === 'projects'"
        @select="selectProject(project.id)"
      />
    </div>
    <button class="add-project" @click="$emit('create')"><Plus :size="15" /> 新建项目</button>
    <div class="sidebar-bottom">
      <div class="sidebar-footer">
        <HardDrive :size="17" />
        <div>本地工作空间<small>项目与证据保存在此设备</small></div>
      </div>
    </div>
  </aside>
</template>
