<script setup lang="ts">
import { computed, onMounted, ref, type Component } from 'vue';
import { AlertCircle, CheckCircle2, X, Orbit } from 'lucide-vue-next';
import AppSidebar from './components/sidebar/AppSidebar.vue';
import ProjectDialog from './components/projects/ProjectDialog.vue';
import ProjectWelcome from './components/projects/ProjectWelcome.vue';
import ProjectRecoveryDialog from './components/projects/ProjectRecoveryDialog.vue';
import { navigation } from './lib/navigation';
import { useWorkspace } from './composables/useWorkspace';
import type { Project } from './types';
import NotificationStack from './components/ui/NotificationStack.vue';
const { state, init, dismiss, undoDelete } = useWorkspace();
const activePage = computed(() => navigation.find((page) => page.id === state.page)!);
const activeComponent = computed<Component>(() => activePage.value.component);
const recoveryOpen = ref(false);
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
  <div class="app-shell spatial-app">
    <AppSidebar @create="create" @recover="recoveryOpen = true" />
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
      <ProjectWelcome v-else :reconnect="Boolean(state.error)" @create="create" @retry="init" />
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
      ><button
        v-if="state.deletedProject && !state.error"
        class="text-button"
        :disabled="state.busy"
        @click="undoDelete"
      >
        撤销删除</button
      ><button
        v-if="state.recoveryRestored?.refreshError"
        class="text-button"
        @click="recoveryOpen = true"
      >
        查看恢复结果</button
      ><button class="icon-button" aria-label="关闭提示" @click="dismiss">
        <X :size="15" />
      </button>
    </div>
    <ProjectDialog v-if="dialogOpen" :project="editing" @close="dialogOpen = false" />
    <ProjectRecoveryDialog v-if="recoveryOpen" @close="recoveryOpen = false" />
    <NotificationStack />
  </div>
</template>
