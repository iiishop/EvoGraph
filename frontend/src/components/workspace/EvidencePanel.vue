<script setup lang="ts">
import { computed } from 'vue';
import { ShieldCheck, Clock3, ChevronRight } from 'lucide-vue-next';
import type { Evidence, Project } from '../../types';
import { formatTime } from '../../lib/presentation';
const props = defineProps<{ project: Project }>();
type StoredEvidence = Evidence & { provider?: string; request_id?: string };
type ReportCheck = { behavior_id: string; result: string; method: string; evidence: string };
type ExternalReport = { provider: string; summary: string; checks: ReportCheck[] };
function readReport(evidence: Evidence): ExternalReport | null {
  if (evidence.command.length) return null;
  try {
    const report = JSON.parse(evidence.output);
    if (
      report &&
      typeof report.provider === 'string' &&
      typeof report.summary === 'string' &&
      Array.isArray(report.checks) &&
      report.checks.every(
        (check: unknown) =>
          check &&
          typeof check === 'object' &&
          ['behavior_id', 'result', 'method', 'evidence'].every(
            (key) => typeof (check as Record<string, unknown>)[key] === 'string',
          ),
      )
    )
      return report;
  } catch {
    // Older local-command evidence remains readable even when output is not JSON.
  }
  return null;
}
const records = computed(() =>
  [...props.project.evidence].reverse().map((e: StoredEvidence) => {
    const report = readReport(e);
    return { evidence: e, report, external: !!e.request_id || !!report };
  }),
);
function resultLabel(result: string) {
  return (
    ({ PASS: '通过', FAIL: '未通过', ERROR: '异常' } as Record<string, string>)[result] || result
  );
}
function milestoneTitle(id: string) {
  return props.project.milestones.find((milestone) => milestone.id === id)?.title || id;
}
function behaviorTitle(id: string) {
  return props.project.behaviors.find((behavior) => behavior.id === id)?.statement || id;
}
</script>
<template>
  <section class="evidence-panel">
    <div class="panel-heading">
      <div>
        <h3>当前结论，有迹可循</h3>
        <p>保留验收来源与逐项证据。基线或验收契约变化后，历史报告需要重新验证。</p>
      </div>
      <span class="subtle-tag">{{ project.evidence.length }} 条记录</span>
    </div>
    <div v-if="!project.evidence.length" class="empty-state compact">
      <ShieldCheck :size="30" />
      <h3>还没有验证证据</h3>
      <p>在里程碑的「任务流程」中，将验收提示词交给外部 Agent，再导入它返回的 JSON 报告。</p>
      <p>报告与逐项检查结果会保存在这里；EvoGraph 不在本地执行验收命令。</p>
    </div>
    <details v-for="{ evidence: e, report, external } in records" :key="e.id" class="evidence-item">
      <summary>
        <ChevronRight :size="14" class="evidence-chevron" aria-hidden="true" />
        <span class="result-dot" :class="e.result.toLowerCase()"></span
        ><strong :title="e.milestone_id">{{ milestoneTitle(e.milestone_id) }}</strong
        ><span>{{ resultLabel(e.result) }}</span
        ><span class="subtle-tag" :class="{ amber: project.evidence_validity[e.id] === 'STALE' }">{{
          project.evidence_validity[e.id] === 'CURRENT' ? '匹配当前版本' : '历史 · 需重新验收'
        }}</span
        ><small
          ><Clock3 :size="12" /> {{ formatTime(e.created_at)
          }}<template v-if="!external && e.command.length"> · {{ e.duration }}s</template></small
        >
      </summary>
      <div class="evidence-body">
        <p class="evidence-origin">
          {{ external ? '外部验收报告' : e.command.length ? '历史本地命令记录' : '历史验收记录' }}
          <template v-if="external">
            · 来源：{{ report?.provider || e.provider || '未记录' }}</template
          >
        </p>
        <p class="muted">
          {{ e.milestone_id }} · 基线 {{ e.baseline_id }} · 覆盖
          {{ e.behavior_revision_ids.length }} 个行为版本
        </p>
        <template v-if="report">
          <p class="report-summary">{{ report.summary }}</p>
          <ol class="report-checks">
            <li v-for="(check, index) in report.checks" :key="index">
              <div class="check-heading">
                <span class="result-dot" :class="check.result.toLowerCase()"></span>
                <strong>{{ behaviorTitle(check.behavior_id) }}</strong>
                <span>{{ resultLabel(check.result) }}</span>
              </div>
              <p class="check-label">检查方式</p>
              <pre>{{ check.method }}</pre>
              <p class="check-label">证据与观察</p>
              <pre>{{ check.evidence }}</pre>
            </li>
          </ol>
          <details class="raw-report">
            <summary>查看原始 JSON 报告</summary>
            <pre>{{ e.output }}</pre>
          </details>
        </template>
        <template v-else>
          <code v-if="e.command.length">{{ JSON.stringify(e.command) }}</code>
          <pre>{{ e.output }}</pre>
        </template>
        <p v-if="external" class="muted">
          来源为报告中的自述。EvoGraph 记录外部结论，不代表已独立执行或复核这些检查。
        </p>
      </div>
    </details>
  </section>
</template>
<style scoped>
.evidence-item > summary {
  flex-wrap: wrap;
}
.evidence-item > summary > strong {
  overflow-wrap: anywhere;
}
.evidence-chevron,
.result-dot {
  flex-shrink: 0;
}
.evidence-item[open] > summary > .evidence-chevron {
  transform: rotate(90deg);
}
.evidence-body > .evidence-origin {
  color: #526d55;
  font-size: 11px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.evidence-body > .report-summary {
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.report-checks {
  list-style: none;
  padding: 0;
  margin: 14px 0;
}
.report-checks > li {
  border-top: 1px solid #e5ece0;
  padding-top: 12px;
  margin-top: 12px;
}
.check-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}
.check-heading > strong {
  flex: 1;
  overflow-wrap: anywhere;
}
.check-heading > span:last-child {
  flex-shrink: 0;
}
.check-label {
  font-size: 10px;
  color: #7d8d82;
  margin: 10px 0 5px;
}
.raw-report > summary {
  display: list-item;
  padding: 7px 0;
}
</style>
