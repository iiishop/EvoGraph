<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { Project, Milestone } from '../../types';
import { useWorkspace } from '../../composables/useWorkspace';
import { useAgent } from '../../composables/useAgent';
import { useNotifications } from '../../composables/useNotifications';
import ObligationItem from './ObligationItem.vue';
const props = defineProps<{ project: Project; milestone: Milestone }>();
const { state, perform, selectNode } = useWorkspace();
const agent = useAgent();
const report = ref(''),
  prompt = ref(''),
  error = ref(''),
  copied = ref(false);
const params = computed(() => ({ project_id: props.project.id, milestone_id: props.milestone.id }));
const ready = computed(() => props.project.readiness[props.milestone.id]);
const stage = computed(() =>
  props.milestone.status === 'VERIFIED_COMPLETE'
    ? 3
    : props.milestone.status === 'AWAITING_ACCEPTANCE'
      ? 2
      : props.milestone.lease_active
        ? 1
        : 0,
);
watch(
  () => props.milestone.id,
  () => {
    report.value = '';
    prompt.value = '';
    error.value = '';
    copied.value = false;
  },
);
async function handoff(action: string) {
  error.value = '';
  copied.value = false;
  const result = await perform<{ prompt: string }>(action, params.value);
  if (!result) return;
  prompt.value = result.prompt;
  if (action === 'verification.export')
    useNotifications().push(`「${props.milestone.title}」开始等待外部验收`);
  try {
    await navigator.clipboard.writeText(result.prompt);
    copied.value = true;
  } catch {
    error.value = '剪贴板不可用，请从下方选择并复制提示词。';
  }
}
async function importReport() {
  error.value = '';
  try {
    const parsed = JSON.parse(
      report.value
        .trim()
        .replace(/^```(?:json)?\s*/, '')
        .replace(/\s*```$/, ''),
    );
    const result = await perform<{ result: string }>('verification.import', {
      ...params.value,
      report: parsed,
    });
    if (result) {
      useNotifications().push(
        result.result === 'PASS'
          ? `「${props.milestone.title}」验收通过，任务已释放`
          : `「${props.milestone.title}」验收未通过，保留任务继续制作`,
      );
      report.value = '';
      prompt.value = '';
    } else error.value = state.error;
  } catch {
    error.value = '报告不是有效 JSON，请粘贴外部 Agent 返回的完整报告。';
  }
}
function investigate() {
  agent.send(
    props.project.id,
    `调查里程碑 ${props.milestone.id} 的前置条件。读取源码并使用 resolve_investigation 记录真实依据；只对无法自行解决的信息或关键决策提问。`,
  );
}
</script>
<template>
  <div class="task-workflow">
    <ol class="workflow-steps" aria-label="任务流程">
      <li
        v-for="(label, i) in ['条件', '制作', '验收', '完成']"
        :key="label"
        :class="{ active: i === stage, done: i < stage }"
        :aria-current="i === stage ? 'step' : undefined"
      >
        <span>{{ i < stage ? '✓' : i + 1 }}</span
        >{{ label }}
      </li>
    </ol>
    <section v-if="stage === 0" class="task-next">
      <h3>先确认任务可以开始</h3>
      <p>依赖、调查依据与资源检查通过后，领取任务。</p>
      <ul v-if="ready.blockers.length" class="blockers">
        <li v-for="item in ready.blockers" :key="item">{{ item }}</li>
      </ul>
      <p v-else class="positive">前置条件已满足，可以领取。</p>
      <button
        class="button secondary"
        :disabled="state.busy"
        @click="perform('baseline.refresh', { project_id: project.id }, '已重新检查基线与前置条件')"
      >
        检查前置条件</button
      ><button
        class="button primary"
        :disabled="state.busy || !ready.safe_to_execute"
        @click="perform('milestone.start', params, '任务已领取，资源已锁定')"
      >
        领取任务
      </button>
    </section>
    <section v-else-if="stage === 1" class="task-next">
      <h3>交给外部 Agent 制作</h3>
      <p>复制任务范围与设计约束。制作完成后，生成绑定最新基线的验收提示词。</p>
      <button
        class="button secondary"
        :disabled="state.busy"
        @click="handoff('implementation.export')"
      >
        复制制作提示词</button
      ><button
        class="button primary"
        :disabled="state.busy"
        @click="handoff('verification.export')"
      >
        制作完成，复制验收提示词
      </button>
    </section>
    <section v-else-if="stage === 2" class="task-next">
      <h3>等待外部验收报告</h3>
      <p>将提示词交给外部 Agent，粘贴它返回的 JSON。全部行为通过后自动完成并释放任务。</p>
      <button
        class="button secondary"
        :disabled="state.busy"
        @click="handoff('verification.export')"
      >
        重新生成验收提示词</button
      ><small>重新生成会使之前的验收请求失效。</small
      ><label class="report-label" for="acceptance-report">验收报告</label
      ><textarea
        id="acceptance-report"
        v-model="report"
        rows="7"
        spellcheck="false"
        placeholder="粘贴完整 JSON 报告"
      /><button
        class="button primary"
        :disabled="state.busy || !report.trim()"
        @click="importReport"
      >
        导入报告并更新状态</button
      ><small>这是外部报告的结论；EvoGraph 不在本地运行验收。</small>
    </section>
    <section v-else class="task-next completed-receipt">
      <h3>验收通过，任务已释放</h3>
      <p>外部报告已归档，可在「记录」中查看。基线变化后会重新判断证据有效性。</p>
    </section>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <details v-if="prompt" open class="handoff-prompt">
      <summary>{{ copied ? '已复制提示词' : '外部 Agent 提示词' }}</summary>
      <textarea
        :value="prompt"
        readonly
        rows="7"
        aria-label="外部 Agent 提示词"
        @focus="($event.target as HTMLTextAreaElement).select()"
      />
    </details>
    <details class="task-conditions" :open="stage === 0">
      <summary>前置条件与调查依据</summary>
      <div v-for="id in milestone.dependencies" :key="id" class="dependency-item">
        <button @click="selectNode(id)">{{ id }}</button
        ><small>{{ milestone.dependency_reasons[id] }}</small>
      </div>
      <p class="muted">资源：{{ milestone.resources.join('、') || '未声明独立资源' }}</p>
      <button
        v-if="milestone.obligations.some((o) => !o.resolved)"
        class="button secondary"
        :disabled="state.busy || !!project.question"
        @click="investigate"
      >
        让 Agent 调查</button
      ><ObligationItem
        v-for="item in milestone.obligations"
        :key="milestone.id + item.id"
        :obligation="item"
        :project-id="project.id"
        :milestone-id="milestone.id"
      />
    </details>
    <button
      v-if="milestone.lease_active"
      class="text-button release-task"
      :disabled="state.busy"
      @click="perform('milestone.release', params, '任务已释放，未标记为完成')"
    >
      暂时释放任务
    </button>
  </div>
</template>
