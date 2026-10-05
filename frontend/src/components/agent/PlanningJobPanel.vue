<script setup lang="ts">
import { computed, ref } from 'vue';
import { useAgent } from '../../composables/useAgent';
import { useWorkspace } from '../../composables/useWorkspace';
import type { PlanningJobView } from '../../types';

const props = defineProps<{ projectId: string; job: PlanningJobView }>();
const emit = defineEmits<{ authorize: [] }>();
const agent = useAgent();
const workspace = useWorkspace();
const refreshing = ref(false);
const acting = ref(false);
const statusLabels: Record<PlanningJobView['status'], string> = {
  authorized: '已授权',
  running: '进行中',
  paused: '已暂停',
  stopped: '已停止',
  applied: '方案已应用',
  cancelled: '已取消',
};
const stopLabels: Record<string, string> = {
  phase_budget_boundary: '本阶段额度已用完',
  aggregate_phase_limit: '已达总阶段上限',
  aggregate_call_limit: '已达总调用上限',
  aggregate_input_limit: '已达总输入上限',
  phase_not_admitted: '阶段未获准启动',
  provider_timeout: '模型响应超时',
  provider_incomplete_or_uncertain: '模型响应不完整或结果待确认',
  request_input_limit: '本次请求输入超出限制',
  output_limit: '模型输出达到限制',
  held_or_invalid_manifest: '传输清单暂缓或无效',
  review_not_accepted: '评审尚未通过',
  invalid_tool_output: '工具输出无效',
  no_safe_progress: '没有可安全推进的进展',
  question: '等待回答问题',
  phase_limit: '已达阶段上限',
  max_phases: '已达总阶段上限',
  call_limit: '已达调用上限',
  max_calls: '已达总调用上限',
  input_limit: '已达输入上限',
  max_input_bytes: '已达总输入上限',
  phase_budget_exhausted: '本阶段额度已用完',
  aggregate_budget_exhausted: '总额度已用完',
  budget_exhausted: '额度已用完',
  authorization_required: '需要新的额度授权',
  cancelled: '用户已取消',
  applied: '方案已应用',
  stale_pins: '保存状态已变化',
  no_progress: '本阶段未取得进展',
  provider_error: '模型调用失败',
};
const canContinue = computed(
  () =>
    props.job.status === 'paused' &&
    props.job.can_continue &&
    !props.job.authorization_needed &&
    Boolean(props.job.continue_pins),
);
const canCancel = computed(() => !['cancelled', 'applied'].includes(props.job.status));
const unavailable = computed(() => acting.value || agent.state.running || workspace.state.busy);
const number = (value: number) => value.toLocaleString('en-US');
async function continueJob() {
  if (unavailable.value || !canContinue.value || !props.job.continue_pins) return;
  acting.value = true;
  try {
    await agent.continuePlanningJob(props.projectId, props.job.id, props.job.continue_pins);
  } finally {
    acting.value = false;
  }
}
async function cancelJob() {
  if (!canCancel.value || agent.state.cancellingJobId) return;
  await agent.cancelPlanningJob(props.projectId, props.job.id);
}
async function refresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await workspace.refresh();
  } catch (error) {
    workspace.setError(error instanceof Error ? error.message : '作业状态刷新失败');
  } finally {
    refreshing.value = false;
  }
}
</script>

<template>
  <section class="planning-job-panel" aria-label="有界规划作业">
    <div class="planning-job-heading">
      <strong>有界规划作业 · {{ statusLabels[job.status] }}</strong>
      <button type="button" class="text-button" :disabled="refreshing" @click="refresh">
        {{ refreshing ? '刷新中…' : '刷新状态' }}
      </button>
    </div>
    <small class="planning-job-id">{{ job.id }} · 来源 {{ job.source_id }}</small>
    <div class="planning-job-progress" aria-live="polite" aria-atomic="true">
      <p>
        已保存检查点：保留 {{ number(job.progress.retained_checkpoints) }} · 新增
        {{ number(job.progress.new_checkpoints) }}
      </p>
      <p>
        生成单元：已完成 {{ number(job.progress.completed_units) }} · 可执行
        {{ number(job.progress.runnable_units) }} · 暂缓 {{ number(job.progress.held_units) }} ·
        剩余 {{ number(job.progress.remaining_units) }}
      </p>
      <small>以上为规划传输的生成单元进度，不代表编码里程碑完成</small>
      <p>
        阶段 {{ number(job.phase_number) }} / {{ number(job.limits.max_phases) }} · 本阶段调用
        {{ number(job.phase_spend.calls) }} / {{ number(job.phase_limits.max_calls) }} · 输入
        {{ number(job.phase_spend.input_bytes) }} /
        {{ number(job.phase_limits.max_total_input_bytes) }} B
      </p>
      <p>
        累计调用 {{ number(job.spend.calls) }} / 已授权 {{ number(job.limits.max_calls) }} ·
        累计输入 {{ number(job.spend.input_bytes) }} / 已授权
        {{ number(job.limits.max_input_bytes) }} B
      </p>
      <p v-if="job.spend.usage_complete">已报告 token：{{ number(job.spend.tokens) }}</p>
      <p v-else>
        Token 用量未完整报告（已报告 {{ number(job.spend.tokens) }}），不能据此推断完整用量或费用
      </p>
      <p>
        剩余调用数下界：{{
          job.progress.remaining_call_lower_bound === null
            ? '未知'
            : number(job.progress.remaining_call_lower_bound)
        }}<small> · 下界不是完成保证</small>
      </p>
      <p v-if="job.stop_reason" class="planning-job-stop">
        停止原因：{{ stopLabels[job.stop_reason] || '作业已停止，请检查原因' }}（{{
          job.stop_reason
        }}）
      </p>
      <p v-if="job.authorization_needed" class="planning-job-stop">
        需要新授权；不会自动增加额度。请确认当前状态和新的实质输入后再启动。
      </p>
    </div>
    <div class="planning-job-actions">
      <button
        v-if="canContinue"
        type="button"
        class="button secondary"
        :disabled="unavailable"
        @click="continueJob"
      >
        在已授权额度内继续
      </button>
      <button
        v-if="job.authorization_needed"
        type="button"
        class="button secondary"
        :disabled="unavailable"
        @click="emit('authorize')"
      >
        查看新作业授权
      </button>
      <button
        v-if="canCancel"
        type="button"
        class="text-button"
        :disabled="Boolean(agent.state.cancellingJobId)"
        @click="cancelJob"
      >
        {{ agent.state.cancellingJobId === job.id ? '正在取消…' : '取消作业' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.planning-job-panel {
  border: 1px solid var(--line, #dbe1e5);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  flex: 0 1 190px;
  min-width: 0;
  min-height: 112px;
  max-height: min(240px, 30dvh);
  overflow: hidden;
}
.planning-job-heading,
.planning-job-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
}
.planning-job-heading {
  justify-content: space-between;
}
.planning-job-id {
  flex-shrink: 0;
  display: block;
  overflow-wrap: anywhere;
  color: var(--muted);
}
.planning-job-progress {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.planning-job-progress p {
  margin: 5px 0;
  overflow-wrap: anywhere;
}
.planning-job-progress small {
  color: var(--muted);
}
.planning-job-stop {
  font-weight: 600;
}
.planning-job-actions {
  margin-top: 8px;
}
.planning-job-actions .button {
  font-size: 12px;
}
</style>
