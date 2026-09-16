<script setup lang="ts">
import { ref } from 'vue';
import { FolderOpen } from 'lucide-vue-next';
import AppModal from '../ui/AppModal.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import type { Project } from '../../types';
const props = defineProps<{ project?: Project }>();
const emit = defineEmits<{ close: [] }>();
const { state, perform, selectProject } = useWorkspace();
const requestId = crypto.randomUUID();
const name = ref(props.project?.name ?? ''),
  description = ref(props.project?.description ?? ''),
  repository = ref(props.project?.repository ?? '');
async function save() {
  const result = await perform<Project>(
    props.project ? 'projects.update' : 'projects.create',
    {
      ...(props.project ? { project_id: props.project.id } : { request_id: requestId }),
      name: name.value,
      description: description.value,
      repository: repository.value,
    },
    '项目已保存',
  );
  if (result) {
    await selectProject(result.id);
    emit('close');
  }
}
</script>
<template>
  <AppModal :title="project ? '项目设置' : '创建新项目'" @close="emit('close')"
    ><form class="form-stack" @submit.prevent="save">
      <p class="muted">连接一个本地仓库，从当前状态规划下一步演化。</p>
      <label
        >项目名称<input
          v-model="name"
          autofocus
          required
          maxlength="100"
          placeholder="例如：我的阅读工作台" /></label
      ><label
        >项目描述<textarea
          v-model="description"
          rows="3"
          maxlength="4000"
          placeholder="这个项目要解决什么问题？"
        /></label
      ><label
        ><span><FolderOpen :size="15" /> 本地仓库路径</span
        ><input v-model="repository" placeholder="F:\Projects\my-project" /><small
          >可以稍后连接。建立基线后，仓库路径不可更换。</small
        ></label
      >
      <p v-if="state.error" class="inline-error" role="alert">
        {{ state.error }}
      </p>
      <footer class="form-footer">
        <button type="button" class="button secondary" @click="emit('close')">取消</button
        ><button class="button primary" :disabled="state.busy || !name.trim()">
          {{ state.busy ? '保存中…' : '保存项目' }}
        </button>
      </footer>
    </form></AppModal
  >
</template>
