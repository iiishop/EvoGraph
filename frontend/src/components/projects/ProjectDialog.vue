<script setup lang="ts">
import { computed, onUnmounted } from 'vue';
import { FolderOpen, LockKeyhole, CheckCircle2 } from 'lucide-vue-next';
import AppModal from '../ui/AppModal.vue';
import { useWorkspace } from '../../composables/useWorkspace';
import { useProjectForm } from '../../composables/useProjectForm';
import type { Project } from '../../types';
const props = defineProps<{ project?: Project }>();
const emit = defineEmits<{ close: [] }>();
const { state } = useWorkspace();
const session = useProjectForm(props.project);
const { form, pending, editable, repositoryLocked } = session;
let mounted = true;
onUnmounted(() => {
  mounted = false;
});
const platform = typeof navigator === 'undefined' ? '' : navigator.userAgent;
const repositoryPlaceholder = /Windows/i.test(platform)
  ? '例如：C:\\Projects\\my-project'
  : /Macintosh|Mac OS X/i.test(platform)
    ? '例如：/Users/你的用户名/Projects/my-project'
    : '例如：/home/你的用户名/projects/my-project';
const repositoryStatus = computed(() => {
  if (repositoryLocked) return '路径已锁定';
  if (!form.fields.repository.trim()) return '暂不连接 · 可留空，稍后连接';
  return form.fields.repository.trim() === props.project?.repository
    ? '已关联仓库'
    : '保存时检查目录';
});
function close(finish = false) {
  if (session.close(finish)) emit('close');
}
async function save() {
  const opened = await session.submit();
  if (opened && mounted) close();
}
async function open() {
  const opened = await session.open();
  if (opened && mounted) close();
}
</script>
<template>
  <AppModal
    :title="project ? '项目设置' : '创建新项目'"
    class="project-entry-modal"
    :class="{ 'is-create': !project }"
    :close-disabled="pending"
    @close="close()"
  >
    <form class="form-stack project-entry-form" :aria-busy="pending" @submit.prevent="save">
      <p class="project-entry-intro">先描述想做的项目，也可以连接已有代码，从当前状态开始规划。</p>
      <label
        >项目名称
        <input
          v-model="form.fields.name"
          autofocus
          required
          :disabled="!editable || pending"
          maxlength="100"
          placeholder="例如：我的阅读工作台"
        />
      </label>
      <label
        >项目描述
        <textarea
          v-model="form.fields.description"
          rows="3"
          maxlength="4000"
          :disabled="!editable || pending"
          placeholder="这个项目要解决什么问题？"
        />
      </label>
      <label>
        <span><FolderOpen :size="15" /> 本地仓库路径 <span class="optional-label">选填</span></span>
        <input
          v-model="form.fields.repository"
          :placeholder="repositoryPlaceholder"
          :readonly="repositoryLocked"
          :disabled="!editable || pending"
          :class="{ 'repository-locked': repositoryLocked }"
          aria-describedby="repository-help"
          spellcheck="false"
          autocomplete="off"
        />
        <span class="repository-status"
          ><LockKeyhole v-if="repositoryLocked" :size="12" />{{ repositoryStatus }}</span
        >
      </label>
      <aside id="repository-help" class="project-repository-guide">
        <template v-if="project?.baselines.length">
          已建立 B{{ project.baselines.at(-1)?.number }}
          基线，路径已锁定以保留证据对应关系。更换仓库请创建新项目；名称与描述仍可修改。
        </template>
        <template v-else-if="repositoryLocked">请先释放在途任务，再更换仓库路径。</template>
        <template v-else>
          填写运行 EvoGraph
          的电脑上的完整文件夹路径。保存后点击「读取基线」扫描代码；已配置模型时会调用它分析源码。首次建立基线前可更换路径。
        </template>
      </aside>
      <section v-if="form.saved" class="project-save-outcome" role="status" aria-live="polite">
        <h3 v-if="form.saved.outcome === 'existing_repository'">该仓库已有项目</h3>
        <h3 v-else-if="form.saved.project.archived">原创建请求已找到，项目目前已删除</h3>
        <h3 v-else>
          <CheckCircle2 :size="17" />{{ form.updateRead ? '当前内容已核对' : '项目已保存' }}
        </h3>
        <strong>{{ form.saved.project.name }}</strong>
        <p v-if="form.saved.outcome === 'existing_repository'">
          新填写的名称和描述没有应用。可以打开原项目，或继续编辑这份草稿。
        </p>
        <p v-else-if="form.saved.project.archived">
          不会重新创建或自动恢复。请在「已删除项目」中查看并按需恢复。
        </p>
        <p v-else-if="form.saved.outcome === 'reused_request'">
          已找回原请求对应的项目。打开后可检查当前内容；没有创建重复项目。
        </p>
        <p v-else>名称和描述已保存。打开后，在同一个输入框继续描述目标、补充约束或调整方向。</p>
        <dl v-if="form.saved.outcome === 'existing_repository'">
          <dt>原项目描述</dt>
          <dd>{{ form.saved.project.description || '未填写' }}</dd>
          <dt>仓库</dt>
          <dd class="project-saved-path">{{ form.saved.project.repository }}</dd>
        </dl>
      </section>
      <p v-if="form.warning" class="project-save-warning" role="status">{{ form.warning }}</p>
      <p v-if="form.error" class="inline-error" role="alert">{{ form.error }}</p>
      <section
        v-if="form.updateRead && !form.saved"
        class="project-save-outcome"
        aria-label="当前保存的项目内容"
      >
        <h3>当前保存的内容</h3>
        <strong>{{ form.updateRead.name }}</strong>
        <p>{{ form.updateRead.description || '未填写描述' }}</p>
        <p class="project-saved-path">{{ form.updateRead.repository || '未连接仓库' }}</p>
      </section>
      <footer class="form-footer project-entry-footer">
        <span v-if="editable" class="project-entry-local">本地保存 · 没有仓库也可以先规划</span>
        <template v-if="form.saved">
          <button
            v-if="form.saved.outcome === 'existing_repository'"
            type="button"
            class="button secondary"
            :disabled="pending"
            @click="session.continueEditing"
          >
            继续编辑
          </button>
          <button
            v-else
            type="button"
            class="button secondary"
            :disabled="pending"
            @click="close(true)"
          >
            完成，稍后打开
          </button>
          <button
            v-if="!form.saved.project.archived"
            type="button"
            class="button primary"
            :disabled="state.busy || pending"
            @click="open"
          >
            {{
              pending
                ? '正在打开…'
                : form.saved.outcome === 'existing_repository'
                  ? '打开现有项目'
                  : '打开已保存项目'
            }}
          </button>
        </template>
        <template v-else-if="form.phase === 'uncertain' || (form.phase === 'opening' && project)">
          <button
            v-if="form.updateRead"
            type="button"
            class="button secondary"
            :disabled="pending"
            @click="session.continueEditing"
          >
            继续编辑
          </button>
          <button
            v-else
            type="button"
            class="button secondary"
            :disabled="pending"
            @click="close()"
          >
            稍后确认
          </button>
          <button
            v-if="project"
            type="button"
            class="button primary"
            :disabled="state.busy || pending"
            @click="session.checkUpdate"
          >
            {{ pending ? '正在核对…' : '读取并核对结果' }}
          </button>
          <button v-else type="submit" class="button primary" :disabled="state.busy || pending">
            重试原创建请求
          </button>
        </template>
        <template v-else>
          <button type="button" class="button secondary" :disabled="pending" @click="close()">
            取消
          </button>
          <button
            type="submit"
            class="button primary"
            :disabled="state.busy || pending || !form.fields.name.trim()"
          >
            {{ pending ? '保存中…' : project ? '保存修改' : '创建项目' }}
          </button>
        </template>
      </footer>
    </form>
  </AppModal>
</template>
