import type { Behavior, Project } from '../types';

export function acceptanceScope(behavior: Behavior): 'target' | 'milestone' {
  return behavior.acceptance_scope ?? 'target';
}

export function acceptanceSummary(project: Project) {
  // The current target is the acceptance contract. Neither scope metadata nor
  // graph position can reconstruct it, especially after a target revision.
  const targetIds = new Set(project.targets.at(-1)?.required_behavior_ids ?? []);
  const verifiedIds = new Set(project.verified_behaviors ?? []);
  const knownIds = new Set(project.behaviors.map((behavior) => behavior.id));
  const activeIds = new Set(
    project.milestones.flatMap((milestone) => milestone.behavior_revision_ids),
  );
  const stepOnlyIds = [...activeIds].filter((id) => knownIds.has(id) && !targetIds.has(id));
  const passed = [...targetIds].filter((id) => verifiedIds.has(id)).length;
  return {
    targetIds,
    passed,
    total: targetIds.size,
    achieved: targetIds.size > 0 && passed === targetIds.size,
    stepOnlyTotal: stepOnlyIds.length,
  };
}
