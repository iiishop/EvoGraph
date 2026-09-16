export const statusLabels: Record<string, string> = {
  PLANNED: '待开始',
  IN_PROGRESS: '进行中',
  REVALIDATION_REQUIRED: '待重验证',
  VERIFIED_COMPLETE: '验收通过',
};
export const eventLabels: Record<string, string> = {
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
