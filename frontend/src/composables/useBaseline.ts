import type { Project } from '../types';
import { useWorkspace } from './useWorkspace';
import { useAgent } from './useAgent';

/** One entry point for refreshing evidence and reconstructing implemented deliverables. */
export function useBaseline() {
  const workspace = useWorkspace();
  async function reconstruct(project: Project) {
    if (!workspace.state.settings?.provider) {
      workspace.setError('基线已读取。请先配置 Provider，再点击「倒推已实现里程碑」。');
      return;
    }
    if (project.question) {
      workspace.setError('请先在下方回答待确认问题，再倒推基线里程碑。');
      return;
    }
    await useAgent().send(
      project.id,
      '请读取当前基线和实际源码，用 reconstruct_baseline_milestones 倒推当前已实现的完整里程碑图。' +
        '按独立可合并的交付能力划分，提供目标、范围、可验证的行为契约及逐项源码依据，整理真实前置依赖。' +
        '复用已有 SRC_ 能力 ID，不按目录生成节点，不虚构功能或历史 PR，不修改未来规划与目标。' +
        '说明调查覆盖范围和局限；未实现的内容不能当作已实现，源码推导不能代替正式验收。',
    );
  }
  async function refresh(project: Project) {
    const updated = await workspace.perform<Project>(
      'baseline.refresh',
      { project_id: project.id },
      '仓库基线已刷新',
    );
    if (
      updated?.baselines.at(-1)?.complete &&
      updated.source_analysis_baseline_id !== updated.baselines.at(-1)?.id
    ) {
      await reconstruct(updated);
    }
  }
  return { refresh, reconstruct };
}
