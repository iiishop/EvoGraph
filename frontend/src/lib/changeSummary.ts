import type { Project } from '../types';

export function changeSummary(before: Project | null, after: Project, label = '图已更新'): string {
  const changes: string[] = [];
  for (const node of after.milestones) {
    const old = before?.milestones.find((n) => n.id === node.id);
    if (!old) changes.push(`新增「${node.title}」`);
    else {
      const fields: [keyof typeof node, string][] = [
        ['title', '标题'],
        ['intent', '目标'],
        ['scope', '范围'],
        ['dependencies', '依赖'],
        ['behavior_revision_ids', '行为要求'],
        ['obligations', '调查依据'],
        ['migration_steps', '迁移步骤'],
        ['status', '状态'],
      ];
      const updated = fields
        .filter(([key]) => JSON.stringify(old[key]) !== JSON.stringify(node[key]))
        .map(([, name]) => name);
      if (updated.length) changes.push(`「${node.title}」更新${updated.join('、')}`);
    }
  }
  for (const node of before?.milestones ?? [])
    if (!after.milestones.some((n) => n.id === node.id)) changes.push(`移除「${node.title}」`);
  if (before?.architectures.length !== after.architectures.length)
    changes.push(`架构更新为 A${after.architectures.at(-1)?.number}`);
  if ((before?.uml_diagrams?.length ?? 0) !== (after.uml_diagrams?.length ?? 0))
    changes.push(`更新「${after.uml_diagrams.at(-1)?.title}」`);
  if (JSON.stringify(before?.diagrams) !== JSON.stringify(after.diagrams) && after.diagrams.length)
    changes.push('设计图已更新');
  if (before?.targets.length !== after.targets.length) changes.push('项目目标已更新');
  return truncate(changes.join('；') || label);
}
export function truncate(text: string, limit = 86) {
  const chars = Array.from(text.replace(/\s+/g, ' ').trim());
  return chars.length > limit ? chars.slice(0, limit - 1).join('') + '…' : chars.join('');
}
