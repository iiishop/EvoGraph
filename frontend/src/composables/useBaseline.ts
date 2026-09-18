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
        '说明调查覆盖范围和局限；未实现的内容不能当作已实现，源码推导不能代替正式验收。' +
        '同时必须按 UML 绘图规范用 save_uml 初始化或更新唯一类图 class_model。' +
        '以真实源码为基础，保留尚未实现的设计，用 mixed 和 design_elements 标明设计类及成员，设计关系标 DESIGN。' +
        '已有设计只有在读到真实实现后才能去掉设计标识。里程碑无需更新时仍检查类图。',
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
      (updated.source_analysis_baseline_id !== updated.baselines.at(-1)?.id ||
        !(
          workspace.state.project?.id === updated.id &&
          workspace.state.project.class_model_state?.current
        ))
    ) {
      await reconstruct(updated);
    }
  }
  async function syncClassModel(project: Project) {
    if (!workspace.state.settings?.provider) {
      workspace.setError('请先配置 Provider，再同步类图。');
      return;
    }
    if (project.question) {
      workspace.setError('请先回答下方待确认问题，再同步类图。');
      return;
    }
    await useAgent().send(
      project.id,
      '请读取当前基线源码和最新设计，用 save_uml 同步唯一类图 class_model，严格遵守 UML 绘图规范。' +
        '保留尚未实现的设计；混合图用 mixed 和 design_elements 为设计类或成员添加 DESIGN 标识，设计关系也标 DESIGN。' +
        '只有实际读取到对应代码才能把设计转为 SRC。不修改里程碑和架构设计。',
    );
  }
  return { refresh, reconstruct, syncClassModel };
}
