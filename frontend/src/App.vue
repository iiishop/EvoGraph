<script setup lang="ts">
import { computed, onMounted, ref, type Component } from 'vue';
import { AlertCircle, CheckCircle2, X, Orbit } from 'lucide-vue-next';
import AppSidebar from './components/sidebar/AppSidebar.vue';
import ProjectDialog from './components/projects/ProjectDialog.vue';
import { navigation } from './lib/navigation';
import { useWorkspace } from './composables/useWorkspace';
import type { Project } from './types';
import NotificationStack from './components/ui/NotificationStack.vue';
const { state, init, dismiss, undoDelete } = useWorkspace();
const activePage = computed(() => navigation.find((page) => page.id === state.page)!);
const activeComponent = computed<Component>(() => activePage.value.component);
const dialogOpen = ref(false),
  editing = ref<Project | undefined>();
function create() {
  editing.value = undefined;
  dialogOpen.value = true;
}
function edit() {
  editing.value = state.project ?? undefined;
  dialogOpen.value = true;
}
onMounted(init);
</script>
<template>
  <div class="app-shell">
    <AppSidebar @create="create" />
    <div class="app-main">
      <div v-if="state.loading" class="loading-screen">
        <Orbit :size="35" class="spinning" />
        <h2>正在打开工作空间</h2>
        <p>读取本地项目与演化记录…</p>
      </div>
      <component
        :is="activeComponent"
        v-else-if="state.project || !activePage.countProjects"
        :key="`${state.page}-${state.project?.id}`"
        v-bind="activePage.countProjects ? { project: state.project } : {}"
        @edit="edit"
      />
      <div v-else class="empty-state">
        <Orbit :size="40" />
        <h2>欢迎来到 EvoGraph</h2>
        <p>从一个项目和一个清晰的目标开始。</p>
        <button class="button primary" @click="state.error ? init() : create()">
          {{ state.error ? '重新连接后端' : '创建项目' }}
        </button>
      </div>
    </div>
    <div
      v-if="state.error || state.notice"
      class="toast"
      :class="{ error: state.error }"
      :role="state.error ? 'alert' : 'status'"
    >
      <AlertCircle v-if="state.error" :size="18" /><CheckCircle2 v-else :size="18" /><span>{{
        state.error || state.notice
      }}</span
      ><button v-if="state.deletedProject && !state.error" class="text-button" @click="undoDelete">
        撤销删除</button
      ><button class="icon-button" aria-label="关闭提示" @click="dismiss">
        <X :size="15" />
      </button>
    </div>
    <ProjectDialog v-if="dialogOpen" :project="editing" @close="dialogOpen = false" />
    <NotificationStack />
  </div>
</template>
