<script setup lang="ts">
import { computed, ref } from 'vue';
import { FolderOpen, LockKeyhole } from 'lucide-vue-next';
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
const saving = ref(false);
const error = ref('');
const repositoryLocked = computed(
  () =>
    !!props.project?.baselines.length ||
    !!props.project?.milestones.some(
      (milestone) =>
        milestone.lease_active ||
        ['IN_PROGRESS', 'REVALIDATION_REQUIRED'].includes(milestone.status),
    ),
);
const platform = typeof navigator === 'undefined' ? '' : navigator.userAgent;
const repositoryPlaceholder = /Windows/i.test(platform)
  ? '例如：C:\\Projects\\my-project'
  : /Macintosh|Mac OS X/i.test(platform)
    ? '例如：/Users/你的用户名/Projects/my-project'
    : '例如：/home/你的用户名/projects/my-project';
const repositoryStatus = computed(() => {
  if (repositoryLocked.value) return '路径已锁定';
  if (!repository.value.trim()) return '暂不连接';
  return repository.value.trim() === props.project?.repository ? '已关联仓库' : '保存时检查目录';
});
function close() {
  if (!saving.value) emit('close');
}
async function save() {
  if (saving.value || state.busy || !name.value.trim()) return;
  saving.value = true;
  error.value = '';
  try {
    const result = await perform<Project>(
      props.project ? 'projects.update' : 'projects.create',
      {
        ...(props.project ? { project_id: props.project.id } : { request_id: requestId }),
        name: name.value.trim(),
        description: description.value,
        repository: repositoryLocked.value ? props.project!.repository : repository.value.trim(),
      },
      '项目已保存',
    );
    if (result) {
      await selectProject(result.id);
      emit('close');
    } else error.value = state.error;
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <AppModal :title="project ? '项目设置' : '创建新项目'" @close="close"
    ><form class="form-stack" @submit.prevent="save">
      <p class="muted">先描述想做的项目，也可以连接已有代码，从当前状态开始规划。</p>
      <label
        >项目名称<input
          v-model="name"
          autofocus
          required
          :disabled="saving"
          maxlength="100"
          placeholder="例如：我的阅读工作台" /></label
      ><label
        >项目描述<textarea
          v-model="description"
          rows="3"
          maxlength="4000"
          :disabled="saving"
          placeholder="这个项目要解决什么问题？"
        /></label
      ><label
        ><span><FolderOpen :size="15" /> 本地仓库路径 <span class="optional-label">选填</span></span
        ><input
          v-model="repository"
          :placeholder="repositoryPlaceholder"
          :readonly="repositoryLocked"
          :disabled="saving"
          :class="{ 'repository-locked': repositoryLocked }"
          aria-describedby="repository-help"
          spellcheck="false"
          autocomplete="off"
        />
        <span class="repository-status"
          ><LockKeyhole v-if="repositoryLocked" :size="12" />{{ repositoryStatus }}</span
        >
        <small id="repository-help" v-if="project?.baselines.length">
          已建立 B{{ project.baselines.at(-1)?.number }}
          基线，路径已锁定以保留证据对应关系。更换仓库请创建新项目；名称与描述仍可修改。
        </small>
        <small id="repository-help" v-else-if="repositoryLocked">
          请先释放在途任务，再更换仓库路径。
        </small>
        <small id="repository-help" v-else>
          粘贴运行 EvoGraph
          的电脑上的完整文件夹路径。可留空，稍后连接；保存后点击「读取基线」扫描代码，已配置模型时会自动调用它分析源码。首次建立基线前可更换路径。
        </small></label
      >
      <p v-if="error" class="inline-error" role="alert">
        {{ error }}
      </p>
      <footer class="form-footer">
        <button type="button" class="button secondary" :disabled="saving" @click="close">
          取消</button
        ><button class="button primary" :disabled="state.busy || saving || !name.trim()">
          {{ saving ? '保存中…' : project ? '保存修改' : '创建项目' }}
        </button>
      </footer>
    </form></AppModal
  >
</template>
<style scoped>
.optional-label {
  margin-left: auto;
  color: #87958d;
  font-size: 11px;
  font-weight: 400;
}
.form-stack label > .repository-status {
  color: #617b6a;
  font-size: 11px;
  font-weight: 400;
}
.form-stack input.repository-locked {
  background: #f3f5f3;
  color: #718076;
}
</style>
