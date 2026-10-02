import type { Project, ProjectEvent, TurnSummary } from '../types';

export type { TurnSummary } from '../types';

type RecordValue = Record<string, unknown>;
const record = (value: unknown): value is RecordValue =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const revision = (value: unknown) => Number.isInteger(value) && (value as number) >= 0;
const optionalRevision = (value: unknown) => value === null || revision(value);
const optionalText = (value: unknown) => value === null || text(value);
const list = (value: unknown, valid: (item: unknown) => boolean) =>
  Array.isArray(value) && value.every(valid);
const milestone = (value: unknown) =>
  record(value) && text(value.id) && text(value.title) && strings(value.fields);
const edgeValue = (value: unknown) => record(value) && text(value.reason) && text(value.type);
const edge = (value: unknown) =>
  record(value) && text(value.source) && text(value.target) && edgeValue(value);
const edgeUpdate = (value: unknown) =>
  record(value) &&
  text(value.source) &&
  text(value.target) &&
  strings(value.fields) &&
  edgeValue(value.before) &&
  edgeValue(value.after);

/** Only the versioned, persisted contract can produce a factual turn receipt. */
export function parseTurnSummary(detail: string): TurnSummary | null {
  try {
    const value: unknown = JSON.parse(detail);
    if (
      !record(value) ||
      value.version !== 1 ||
      !text(value.turn_id) ||
      !value.turn_id ||
      !text(value.status) ||
      !['completed', 'waiting', 'stopped', 'failed'].includes(value.status) ||
      typeof value.changed !== 'boolean' ||
      (value.history_warning !== undefined && !text(value.history_warning)) ||
      !revision(value.before_revision) ||
      !revision(value.after_revision) ||
      !record(value.changes)
    )
      return null;
    const { milestones, dependencies, target, architecture, other } = value.changes;
    if (
      !record(milestones) ||
      !list(milestones.added, milestone) ||
      !list(milestones.updated, milestone) ||
      !list(milestones.removed, milestone) ||
      !record(dependencies) ||
      !list(dependencies.added, edge) ||
      !list(dependencies.updated, edgeUpdate) ||
      !list(dependencies.removed, edge) ||
      !strings(other)
    )
      return null;
    if (
      target !== null &&
      (!record(target) ||
        !optionalRevision(target.before_version) ||
        !optionalRevision(target.after_version) ||
        typeof target.statement_changed !== 'boolean' ||
        !record(target.required_behavior_ids) ||
        !strings(target.required_behavior_ids.added) ||
        !strings(target.required_behavior_ids.removed) ||
        !list(
          target.required_behavior_changes,
          (item) =>
            record(item) &&
            text(item.behavior_key) &&
            optionalText(item.before_id) &&
            optionalText(item.after_id) &&
            strings(item.fields),
        ))
    )
      return null;
    if (
      architecture !== null &&
      (!record(architecture) ||
        !optionalRevision(architecture.before_revision) ||
        !optionalRevision(architecture.after_revision))
    )
      return null;
    return value as unknown as TurnSummary;
  } catch {
    return null;
  }
}

/** Events are newest first. Never substitute an older successful turn. */
export function latestTurnSummary(
  source: Pick<Project, 'events' | 'messages'> | ProjectEvent[],
): TurnSummary | null {
  const events = Array.isArray(source) ? source : source.events;
  const event = events?.find((item) => item.kind === 'agent_turn_finished');
  if (!event) return null;
  if (!Array.isArray(source)) {
    const finishedAt = Date.parse(event.created_at);
    if (
      source.messages?.some(
        (item) => item.role === 'user' && Date.parse(item.created_at) > finishedAt,
      )
    )
      return null;
  }
  return parseTurnSummary(event.detail);
}

export const turnFieldLabels: Record<string, string> = {
  title: '标题',
  intent: '交付目标',
  scope: '范围',
  resources: '资源',
  change_types: '变更类型',
  behaviors: '验收标准',
  behavior_revision_ids: '验收标准',
  statement: '验收描述',
  acceptance_scope: '验收归属',
  owner: '所属里程碑',
  architecture_revision: '架构版本',
  pinned_baseline: '任务基线',
  lease_active: '任务领取状态',
  architecture_components: '关联架构',
  attachment_ids: '关联资料',
  dependencies: '前置依赖',
  dependency_reasons: '依赖原因',
  dependency_types: '依赖类型',
  obligations: '调查依据',
  migration_steps: '迁移步骤',
  status: '状态',
  reason: '原因',
  type: '类型',
  revision: '版本',
  origin: '来源',
  source_refs: '源码引用',
  source_behaviors: '源码行为',
  source_baseline_id: '源码基线',
};
export const turnOtherLabels: Record<string, string> = {
  project: '项目信息',
  light_checks: '轻量检查',
  diagrams: '设计图',
  uml_diagrams: '局部类结构',
  source_analysis: '源码分析',
  research: '研究记录',
  proposal: '规划草案',
  plans: '规划记录',
  attachments: '项目资料',
  evidence: '验收记录',
  baselines: '仓库基线',
};
export const dependencyTypeLabels: Record<string, string> = {
  implementation: '实现',
  migration: '迁移',
  verification: '验证',
};
export const turnSummaryStatus = (summary: TurnSummary) =>
  ({
    completed: '本轮完成',
    waiting: '等待你的回答',
    stopped: '已停止',
    failed: '本轮未完成',
  })[summary.status];

export function turnSummaryHasChanges(summary: TurnSummary): boolean {
  // The backend's before/after comparison excludes transient edits and revision churn.
  if (!summary.changed) return false;
  const { milestones, dependencies, target, architecture, other } = summary.changes;
  return Boolean(
    milestones.added.length ||
    milestones.updated.some((item) => item.fields.length) ||
    milestones.removed.length ||
    dependencies.added.length ||
    dependencies.updated.some(
      (item) => item.before.reason !== item.after.reason || item.before.type !== item.after.type,
    ) ||
    dependencies.removed.length ||
    target?.statement_changed ||
    target?.required_behavior_ids.added.length ||
    target?.required_behavior_ids.removed.length ||
    target?.required_behavior_changes.some((item) => item.before_id !== item.after_id) ||
    architecture ||
    other.length,
  );
}

export function turnSummaryHeadline(summary: TurnSummary): string {
  if (!turnSummaryHasChanges(summary)) return '无净变更';
  const { milestones, dependencies, target, architecture, other } = summary.changes;
  const parts: string[] = [];
  if (milestones.added.length) parts.push(`新增 ${milestones.added.length} 个里程碑`);
  if (milestones.updated.length) parts.push(`更新 ${milestones.updated.length} 个里程碑`);
  if (milestones.removed.length) parts.push(`移除 ${milestones.removed.length} 个里程碑`);
  const edgeCount =
    dependencies.added.length + dependencies.updated.length + dependencies.removed.length;
  if (edgeCount) parts.push(`依赖 ${edgeCount} 项`);
  if (target) {
    if (target.statement_changed) parts.push('目标描述');
    const count =
      target.required_behavior_changes.length ||
      target.required_behavior_ids.added.length + target.required_behavior_ids.removed.length;
    if (count) parts.push(`最终验收 ${count} 项`);
  }
  if (architecture) parts.push('架构更新');
  parts.push(...other.map((area) => turnOtherLabels[area] || area));
  return parts.join(' · ') || '已保存变更';
}

export function turnSummaryNotice(summary: TurnSummary): string {
  if (summary.status === 'stopped' || summary.status === 'failed') {
    return turnSummaryHasChanges(summary)
      ? '已保存的部分变更保留，本轮未完成'
      : '本轮未完成，没有净变更；已保存的记录保留';
  }
  if (summary.status === 'waiting') {
    return turnSummaryHasChanges(summary)
      ? '当前变更已保存，等待你的回答后继续'
      : '等待你的回答后继续';
  }
  return turnSummaryHasChanges(summary) ? '本轮规划变更已保存' : '本轮没有净变更';
}

export function turnSummaryMessage(summary: TurnSummary): string {
  return `${turnSummaryStatus(summary)} · ${turnSummaryHeadline(summary)}；${turnSummaryNotice(summary)}`;
}

/** Node history contains only persisted changes that explicitly name this node. */
export function milestoneTurnHistory(project: Pick<Project, 'events'>, milestoneId: string) {
  return (project.events ?? []).flatMap((event) => {
    if (event.kind !== 'agent_turn_finished') return [];
    const summary = parseTurnSummary(event.detail);
    if (!summary || !turnSummaryHasChanges(summary)) return [];
    const milestones = {
      added: summary.changes.milestones.added.filter((item) => item.id === milestoneId),
      updated: summary.changes.milestones.updated.filter((item) => item.id === milestoneId),
      removed: summary.changes.milestones.removed.filter((item) => item.id === milestoneId),
    };
    const involved = (item: { source: string; target: string }) =>
      item.source === milestoneId || item.target === milestoneId;
    const dependencies = {
      added: summary.changes.dependencies.added.filter(involved),
      updated: summary.changes.dependencies.updated.filter(involved),
      removed: summary.changes.dependencies.removed.filter(involved),
    };
    if (
      ![...Object.values(milestones), ...Object.values(dependencies)].some((items) => items.length)
    )
      return [];
    return [
      {
        id: event.id,
        createdAt: event.created_at,
        summary: {
          ...summary,
          changes: { milestones, dependencies, target: null, architecture: null, other: [] },
        },
      },
    ];
  });
}
