<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { X, Play, Copy, ExternalLink, ShieldCheck } from 'lucide-vue-next';
import type { Milestone, Project } from '../../types';
import { useWorkspace } from '../../composables/useWorkspace';
import StatusBadge from '../ui/StatusBadge.vue';
import AssetPreview from '../attachments/AssetPreview.vue';
import DiagramView from '../design/DiagramView.vue';
import ObligationItem from './ObligationItem.vue';
import AgentAssurance from './AgentAssurance.vue';
const props = defineProps<{ milestone: Milestone; project: Project }>();
const { state, selectNode, perform } = useWorkspace();
const commandInput = ref(''),
  localError = ref(''),
  copied = ref(false);
const behaviors = computed(() =>
  props.project.behaviors.filter((b) => props.milestone.behavior_revision_ids.includes(b.id)),
);
const readiness = computed(() => props.project.readiness[props.milestone.id]);
const params = computed(() => ({
  project_id: props.project.id,
  milestone_id: props.milestone.id,
}));
watch(
  () => props.milestone.id,
  () => {
    localError.value = '';
    copied.value = false;
  },
);
async function verify() {
  localError.value = '';
  try {
    const command = JSON.parse(commandInput.value);
    if (!Array.isArray(command) || !command.length || command.some((c) => typeof c !== 'string'))
      throw new Error('请填写 JSON 字符串数组，例如 ["python", "-m", "pytest", "-q"]');
    await perform('milestone.verify', { ...params.value, command }, '验证记录已保存，请查看结果');
  } catch (error) {
    localError.value = String(error);
  }
}
async function copyTask() {
  try {
    await navigator.clipboard.writeText(
      `# ${props.milestone.id} ${props.milestone.title}\n${props.milestone.intent}\n\nScope:\n${props.milestone.scope.join('\n')}\n\nAcceptance:\n${behaviors.value.map((b) => b.statement).join('\n')}\n\nBaseline: ${props.milestone.pinned_baseline ?? 'not pinned'}\n\nImplement only this milestone. Run acceptance checks. Report changed files and results.`,
    );
    copied.value = true;
  } catch {
    localError.value = '剪贴板不可用，请手动复制节点详情。';
  }
}
</script>
<template>
  <aside class="inspector">
    <header>
      <span class="eyebrow">MILESTONE / {{ milestone.id }}</span
      ><button class="icon-button" aria-label="关闭节点详情" @click="selectNode(null)">
        <X :size="17" />
      </button>
    </header>
    <div class="inspector-scroll">
      <h2>{{ milestone.title }}</h2>
      <StatusBadge :status="milestone.status" />
      <p class="intent">{{ milestone.intent }}</p>
      <AgentAssurance :project="project" :milestone="milestone" />
      <section v-if="milestone.architecture_components.length">
        <h4>架构组件</h4>
        <code
          v-for="component in milestone.architecture_components"
          :key="component"
          class="scope-path"
          >{{ component }}</code
        >
      </section>
      <section v-if="milestone.attachment_ids.length">
        <h4>设计参考</h4>
        <AssetPreview
          v-for="asset in project.attachments.filter((a) =>
            milestone.attachment_ids.includes(a.id),
          )"
          :key="asset.id"
          :asset="asset"
          :project-id="project.id"
        />
      </section>
      <section
        v-for="diagram in project.diagrams.filter((d) => d.milestone_ids.includes(milestone.id))"
        :key="diagram.id"
      >
        <h4>{{ diagram.title }}</h4>
        <DiagramView :diagram="diagram" />
      </section>
      <section>
        <h4>变更范围</h4>
        <code v-for="scope in milestone.scope" :key="scope" class="scope-path">{{ scope }}</code>
      </section>
      <section>
        <h4><ShieldCheck :size="14" /> 行为验收</h4>
        <article v-for="behavior in behaviors" :key="behavior.id" class="behavior-item">
          <span>{{ behavior.statement }}</span
          ><small
            >{{ behavior.behavior_key }} · v{{ behavior.version
            }}<span v-if="behavior.supersedes"> · 替代旧版本</span></small
          >
        </article>
      </section>
      <section v-if="milestone.dependencies.length">
        <h4>前置依赖</h4>
        <p v-for="dep in milestone.dependencies" :key="dep" class="dependency-item">
          <button @click="selectNode(dep)">{{ dep }} <ExternalLink :size="11" /></button
          ><small>{{ milestone.dependency_reasons[dep] }}</small>
        </p>
      </section>
      <section>
        <h4>
          调查义务
          <span
            >{{ milestone.obligations.filter((o) => o.resolved).length }}/{{
              milestone.obligations.length
            }}</span
          >
        </h4>
        <ObligationItem
          v-for="obligation in milestone.obligations"
          :key="`${milestone.id}-${obligation.id}`"
          :obligation="obligation"
          :project-id="project.id"
          :milestone-id="milestone.id"
        />
      </section>
      <section>
        <h4>执行条件</h4>
        <div class="readiness-row">
          <span>逻辑就绪</span
          ><strong :class="{ positive: readiness.logical_ready }">{{
            readiness.logical_ready ? '已满足' : '待满足'
          }}</strong>
        </div>
        <div class="readiness-row">
          <span>可安全领取</span
          ><strong :class="{ positive: readiness.safe_to_execute }">{{
            readiness.safe_to_execute ? '是' : '否'
          }}</strong>
        </div>
        <ul v-if="readiness.blockers.length" class="blockers">
          <li v-for="blocker in readiness.blockers" :key="blocker">
            {{ blocker }}
          </li>
        </ul>
        <small class="muted"
          >资源：{{ milestone.resources.join('、') || '未声明，按潜在冲突处理' }}</small
        >
      </section>
      <div class="inspector-actions">
        <button
          class="button primary"
          :disabled="
            state.busy ||
            !readiness.safe_to_execute ||
            milestone.status === 'VERIFIED_COMPLETE' ||
            milestone.status === 'IN_PROGRESS'
          "
          @click="perform('milestone.start', params, '已领取任务并锁定当前基线')"
        >
          <Play :size="14" />{{
            milestone.status === 'REVALIDATION_REQUIRED' ? '重新绑定基线' : '领取里程碑'
          }}</button
        ><button class="button secondary" @click="copyTask">
          <Copy :size="14" />{{ copied ? '已复制' : '复制执行任务' }}
        </button>
      </div>
      <details
        v-if="['IN_PROGRESS', 'REVALIDATION_REQUIRED'].includes(milestone.status)"
        class="verification-form"
      >
        <summary>高级：手动正式验收</summary>
        <p class="muted">将此命令结果作为以上行为的验收依据。命令会在项目目录执行，最长 120 秒。</p>
        <textarea
          v-model="commandInput"
          rows="3"
          aria-label="验收命令 JSON 参数数组"
          spellcheck="false"
        />
        <p v-if="localError" class="inline-error">{{ localError }}</p>
        <button class="button primary full-width" :disabled="state.busy" @click="verify">
          {{ state.busy ? '正在处理…' : '运行并记录验收' }}</button
        ><button
          class="text-button"
          :disabled="state.busy"
          @click="perform('milestone.release', params, '已释放任务')"
        >
          释放任务
        </button>
      </details>
    </div>
  </aside>
</template>
