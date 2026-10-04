"""Pure working-graph validity shared by editors and the planning harness."""

from .dependencies import ancestor_sets
from .models import ProposedMilestone
from .policies import MAX_WORKING_MILESTONES, validate_milestones


def check_graph(project):
    if project.archived:
        raise ValueError("项目已删除")
    if len(project.milestones) > MAX_WORKING_MILESTONES:
        raise ValueError(f"工作图最多支持 {MAX_WORKING_MILESTONES} 个交付里程碑，请拆分项目")
    if not project.milestones:
        return
    by_id = {b.id: b for b in project.behaviors}
    source_ids = {m.id for m in project.source_milestones}
    proposed = []
    for node in project.milestones:
        behaviors = [by_id[bid] for bid in node.behavior_revision_ids]
        if any(b.owner != node.id for b in behaviors):
            raise ValueError("行为验收归属不一致")
        # Validate edge metadata before filtering out read-only SRC nodes.
        # Missing reasons must be a tool validation error, not a KeyError.
        if len(node.dependencies) != len(set(node.dependencies)):
            raise ValueError("同一前置依赖不能重复")
        if set(node.dependency_reasons) - set(node.dependencies):
            raise ValueError("依赖理由引用了不存在的连线")
        for dependency in node.dependencies:
            if not node.dependency_reasons.get(dependency, "").strip():
                raise ValueError(f"{node.id} 的前置依赖 {dependency} 缺少依赖理由")
        dependencies = [d for d in node.dependencies if d not in source_ids]
        proposed.append(
            ProposedMilestone(
                **node.model_dump(
                    include={
                        "id",
                        "title",
                        "intent",
                        "scope",
                        "architecture_components",
                        "resources",
                        "change_types",
                    }
                ),
                dependencies=dependencies,
                dependency_reasons={d: node.dependency_reasons[d] for d in dependencies},
                behaviors=[
                    {
                        "key": b.behavior_key,
                        "statement": b.statement,
                        "acceptance_scope": b.acceptance_scope,
                    }
                    for b in behaviors
                ],
            )
        )
    validate_milestones(proposed)
    # SRC capabilities are read-only but remain real nodes in the complete
    # prerequisite graph used for cycle and reachability validation.
    ancestor_sets(
        {m.id: list(m.dependencies) for m in [*project.source_milestones, *project.milestones]}
    )
