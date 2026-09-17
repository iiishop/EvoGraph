export const statusLabels: Record<string, string> = {
  PLANNED: '待开始',
  IN_PROGRESS: '进行中',
  AWAITING_ACCEPTANCE: '等待验收',
  REVALIDATION_REQUIRED: '待重验证',
  VERIFIED_COMPLETE: '验收通过',
};
export const eventLabels: Record<string, string> = {
  dependencies_reduced: '自动整理依赖',
  acceptance_prepared: '准备外部验收',
  external_acceptance_imported: '导入外部验收报告',
  uml_updated: '维护 UML 设计图',
  graph_edited: '修改里程碑图',
  architecture_updated: '更新架构设计',
  diagram_updated: '更新设计图',
  attachment_uploaded: '上传项目资料',
  agent_turn_finished: '结束本轮规划',
  target_committed: '更新目标',
  agent_answer: '补充需求',
  project_deleted: '移出项目列表',
  project_restored: '恢复项目',
  plan_applied: '应用规划',
  plan_proposed: '生成草案',
  conversation: 'Agent 对话',
  baseline_updated: '更新基线',
  obligation_reviewed: '记录调查',
  execution_started: '领取任务',
  execution_released: '释放任务',
  verification_finished: '完成验证',
  execution_blocked: '执行受阻',
  project_updated: '更新项目',
  proposal_discarded: '放弃草案',
  layout_updated: '保存布局',
};
export function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}

export function eventDetail(event: { kind: string; detail: string }) {
  if (event.kind === 'dependencies_reduced') {
    try {
      return `移除 ${JSON.parse(event.detail).length} 条冗余依赖，前置约束保持不变`;
    } catch {
      return '已自动整理依赖';
    }
  }
  if (event.kind === 'graph_edited') {
    try {
      return JSON.parse(event.detail).summary;
    } catch {
      return '图已更新';
    }
  }
  if (event.kind === 'agent_turn_finished') return '本轮已结束，已完成的修改已保存';
  return event.detail.slice(0, 250) || '状态已保存到本地数据库';
}
