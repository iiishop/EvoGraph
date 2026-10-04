import type { Behavior, Project, ProjectEvent, TurnContractSide, TurnSummary } from '../types';

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

const contractSide = (value: unknown) =>
  record(value) && optionalText(value.active_id) && optionalText(value.required_id);
const contractDetails = (value: unknown) =>
  record(value) &&
  text(value.project_id) &&
  text(value.project_created_at) &&
  optionalRevision(value.before_target_version) &&
  optionalRevision(value.after_target_version) &&
  list(
    value.behaviors,
    (item) =>
      record(item) &&
      optionalText(item.behavior_key) &&
      contractSide(item.before) &&
      contractSide(item.after) &&
      strings(item.fields) &&
      typeof item.restored === 'boolean',
  );

const candidateOutcome = (value: unknown) =>
  record(value) &&
  (value.id === null || (text(value.id) && Boolean(value.id))) &&
  text(value.status) &&
  [
    'generating',
    'reviewing',
    'needs_resolution',
    'ready',
    'applied',
    'stopped',
    'failed',
    'stale',
    'discarded',
    'not_admitted',
  ].includes(value.status) &&
  value.canonical_unchanged === true &&
  text(value.note);

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
      (value.candidate_outcome !== undefined && !candidateOutcome(value.candidate_outcome)) ||
      (value.contract_details !== undefined && !contractDetails(value.contract_details)) ||
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

export interface TurnReceiptSelection {
  projectId: string;
  projectCreatedAt: string;
  eventId: string;
  createdAt: string;
  detail: string;
}
type ReceiptProject = Pick<Project, 'id' | 'created_at' | 'events'>;

/** Capture the chosen persisted event, never an index or the latest turn. */
export function selectTurnReceipt(
  project: ReceiptProject,
  eventId: string,
): TurnReceiptSelection | null {
  const matches = (project.events ?? []).filter(
    (event) => event.id === eventId && event.kind === 'agent_turn_finished',
  );
  if (matches.length !== 1) return null;
  const event = matches[0];
  return {
    projectId: project.id,
    projectCreatedAt: project.created_at,
    eventId: event.id,
    createdAt: event.created_at,
    detail: event.detail,
  };
}

/** Refresh may trim history. Keep that selection unavailable rather than substituting another. */
export function readTurnReceipt(project: ReceiptProject, selected: TurnReceiptSelection) {
  const unavailable = (message: string) => ({ summary: null, unavailable: message });
  if (project.id !== selected.projectId || project.created_at !== selected.projectCreatedAt)
    return unavailable('项目已切换，无法读取此历史回执');
  const matches = (project.events ?? []).filter((event) => event.id === selected.eventId);
  const event = matches.length === 1 ? matches[0] : undefined;
  if (
    !event ||
    event.kind !== 'agent_turn_finished' ||
    event.created_at !== selected.createdAt ||
    event.detail !== selected.detail
  )
    return unavailable('所选历史回执已不在当前记录中，无法读取');
  const summary = parseTurnSummary(event.detail);
  if (!summary) return unavailable('这条历史记录未保存可读取的完整回执');
  const identity = summary.contract_details;
  if (
    identity &&
    (identity.project_id !== project.id || identity.project_created_at !== project.created_at)
  )
    return unavailable('回执与当前项目历史不匹配，无法读取');
  return { summary, unavailable: '' };
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
  target_draft: '未提交目标草案',
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
export function turnSummaryStatus(summary: TurnSummary): string {
  const candidate = summary.candidate_outcome;
  if (candidate) {
    if (!candidate.id || candidate.status === 'not_admitted') return '候选尚未生成';
    return {
      generating: '候选已保留',
      reviewing: '候选待评审',
      needs_resolution: '候选待解决',
      ready: '候选待提交',
      applied: '候选已保留',
      stopped: '候选已停止',
      failed: '候选未完成',
      stale: '候选基于旧版本',
      discarded: '候选已放弃',
    }[candidate.status];
  }
  return {
    completed: turnSummaryHasChanges(summary) ? '变更已保存' : '本轮已结束',
    waiting: '等待你的回答',
    stopped: '已停止',
    failed: '本轮未完成',
  }[summary.status];
}

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
    summary.contract_details?.behaviors.length ||
    other.length,
  );
}

export function turnSummaryHeadline(summary: TurnSummary): string {
  if (summary.candidate_outcome)
    return summary.candidate_outcome.id &&
      !['discarded', 'not_admitted'].includes(summary.candidate_outcome.status)
      ? '该轮候选已保留，未应用到正式方案'
      : '该轮请求未应用到正式方案';
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
  if (summary.contract_details?.behaviors.length && !target?.required_behavior_changes.length)
    parts.push(`验收变更 ${summary.contract_details.behaviors.length} 项`);
  if (architecture) parts.push('架构更新');
  parts.push(...other.map((area) => turnOtherLabels[area] || area));
  return parts.join(' · ') || '已保存变更';
}

export function turnSummaryNotice(summary: TurnSummary): string {
  if (summary.candidate_outcome) {
    const outcome = summary.candidate_outcome;
    return outcome.note || turnSummaryHeadline(summary);
  }
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
          contract_details: undefined,
          changes: { milestones, dependencies, target: null, architecture: null, other: [] },
        },
      },
    ];
  });
}

export type TurnHistory = Pick<Project, 'id' | 'created_at' | 'targets' | 'behaviors' | 'events'>;

/** Legacy receipts need their own persisted event; a key or a latest value is never provenance. */
export function turnHistoryBound(summary: TurnSummary, project?: TurnHistory): boolean {
  if (!project) return false;
  const detail = summary.contract_details;
  if (detail)
    return detail.project_id === project.id && detail.project_created_at === project.created_at;
  return (project.events ?? []).some((event) => {
    if (event.kind !== 'agent_turn_finished') return false;
    const saved = parseTurnSummary(event.detail);
    return (
      saved?.turn_id === summary.turn_id &&
      saved.before_revision === summary.before_revision &&
      saved.after_revision === summary.after_revision &&
      JSON.stringify(saved.changes) === JSON.stringify(summary.changes)
    );
  });
}

export interface TurnContractView {
  key: string;
  label: string;
  fields: string[];
  sides: {
    label: string;
    id: string | null;
    behavior?: Behavior;
    membership: string;
    missing: string;
  }[];
}

export function turnContractViews(summary: TurnSummary, project?: TurnHistory): TurnContractView[] {
  const bound = turnHistoryBound(summary, project);
  const detail = summary.contract_details;
  const side = (value: TurnContractSide, label: string, key: string | null, legacy = false) => {
    const id = value.active_id ?? value.required_id;
    // Exact identity only. Incomplete or inconsistent history is explicitly unavailable.
    const matches = bound
      ? (project?.behaviors ?? []).filter(
          (item) => item.id === id && (key === null || item.behavior_key === key),
        )
      : [];
    const behavior = matches.length === 1 ? matches[0] : undefined;
    let membership = legacy
      ? '旧回执未记录启用状态'
      : value.active_id
        ? '该侧已启用'
        : '该侧未启用';
    if (value.required_id) {
      membership +=
        value.active_id && value.required_id !== value.active_id
          ? ` · 目标仍引用其他版本 ${value.required_id}`
          : ` · 目标引用 ${value.required_id}`;
    } else membership += ' · 未纳入此侧目标';
    const missing = id
      ? bound
        ? `历史验收记录缺失：${id}`
        : '缺少匹配的项目历史，无法读取此版本'
      : legacy
        ? '旧回执未记录此侧启用版本，不能据此判断新增或停用'
        : '本侧未启用，也无目标引用';
    return { label, id, behavior, membership, missing };
  };
  if (detail)
    return detail.behaviors.map((item) => ({
      key:
        item.behavior_key ??
        item.before.active_id ??
        item.after.active_id ??
        item.before.required_id ??
        item.after.required_id ??
        '未知验收',
      label:
        !item.before.active_id && item.after.active_id
          ? item.restored
            ? '恢复启用'
            : '新增验收项'
          : item.before.active_id && !item.after.active_id
            ? '停用验收项'
            : item.fields.length
              ? '修订'
              : '更新版本引用',
      fields: item.fields,
      sides: [
        side(item.before, '变更前', item.behavior_key),
        side(item.after, '变更后', item.behavior_key),
      ],
    }));
  const target = summary.changes.target;
  if (!target) return [];
  const entries = target.required_behavior_changes.map((item) => ({
    ...item,
    lookupKey: item.behavior_key as string | null,
  }));
  for (const [direction, idField] of [
    ['added', 'after_id'],
    ['removed', 'before_id'],
  ] as const) {
    for (const id of target.required_behavior_ids[direction]) {
      if (!entries.some((item) => item[idField] === id))
        entries.push({
          behavior_key: id,
          lookupKey: null,
          before_id: direction === 'removed' ? id : null,
          after_id: direction === 'added' ? id : null,
          fields: [],
        });
    }
  }
  return entries
    .filter((item) => item.before_id !== null || item.after_id !== null)
    .map((item) => ({
      key: item.behavior_key,
      label:
        item.before_id === null
          ? '纳入目标'
          : item.after_id === null
            ? '移出目标'
            : item.fields.length
              ? '修订'
              : '更新版本引用',
      fields: item.fields,
      sides: [
        side({ active_id: null, required_id: item.before_id }, '变更前', item.lookupKey, true),
        side({ active_id: null, required_id: item.after_id }, '变更后', item.lookupKey, true),
      ],
    }));
}

export function turnTargetView(summary: TurnSummary, project?: TurnHistory) {
  const detail = summary.contract_details;
  const target = summary.changes.target;
  if (!detail && !target) return null;
  const bound = turnHistoryBound(summary, project);
  const beforeVersion = detail ? detail.before_target_version : target!.before_version;
  const afterVersion = detail ? detail.after_target_version : target!.after_version;
  const get = (number: number | null) => {
    const matches = bound ? (project?.targets ?? []).filter((item) => item.number === number) : [];
    return matches.length === 1 ? matches[0] : undefined;
  };
  const before = get(beforeVersion),
    after = get(afterVersion);
  const unchanged =
    before && after ? before.statement === after.statement : !target?.statement_changed;
  return {
    unchanged,
    sharedStatement: Boolean(before && after && unchanged),
    sides: [
      { label: '变更前', version: beforeVersion, target: before },
      { label: '变更后', version: afterVersion, target: after },
    ],
  };
}
