"""Deterministic rules. Semantic sufficiency is deliberately not a PASS claim."""

from .models import Milestone, Obligation, PlanProposal, Project, ProposedMilestone

# Explicit non-product declarations, still subject to semantic review. Data or
# operational migration uses "data"; only its runbook may be "documentation".
DECLARED_SUPPORTING_CHANGE_TYPES = frozenset({"documentation", "test"})

# One registry is the extension point for investigation obligations.
CHANGE_POLICIES = {
    "general": [("scope", "确认变更范围与验收方式")],
    "api": [("consumers", "检查 API 消费者"), ("compatibility", "检查接口兼容性与契约")],
    "data": [("migration", "检查数据迁移与回滚"), ("data_consumers", "检查持久化数据消费者")],
    "auth": [
        ("credentials", "检查凭据存储与认证边界"),
        ("negative_paths", "检查失败路径与权限隔离"),
    ],
    **{kind: [] for kind in sorted(DECLARED_SUPPORTING_CHANGE_TYPES)},
}

STRUCTURAL_RELATIONS = {"imports", "calls", "reads_schema", "consumes_api"}


def impact_closure(seeds: set[str], relations: list[dict]) -> tuple[set[str], set[str]]:
    """Edges point from changed resource to its consumer. Co-change is advisory."""
    closure = set(seeds)
    while True:
        added = {
            e["to"] for e in relations if e["type"] in STRUCTURAL_RELATIONS and e["from"] in closure
        }
        if added <= closure:
            break
        closure |= added
    candidates = {
        e["to"] for e in relations if e["type"] == "cochange" and e["from"] in closure
    } - closure
    return closure, candidates


def retirement_disposition(reachable: bool, trace_complete: bool) -> str:
    return "SUPPORTED" if reachable else "ORPHAN_CANDIDATE" if trace_complete else "UNKNOWN"


def obligations(change_types: list[str]) -> list[Obligation]:
    return [
        Obligation(id=key, label=label)
        for kind in dict.fromkeys(["general", *change_types])
        for key, label in CHANGE_POLICIES.get(kind, [])
    ]


def resolve_architecture_components(
    project: Project, proposed: ProposedMilestone, previous: Milestone | None = None
) -> list[str]:
    """Unmapped work is pending association; an empty proposal never clears an existing mapping."""
    components = proposed.architecture_components or (
        previous.architecture_components if previous else []
    )
    architecture = project.architectures[-1] if project.architectures else None
    if set(components) - ({n.id for n in architecture.diagram.nodes} if architecture else set()):
        raise ValueError("引用的架构组件不存在，请先更新架构设计")
    return list(components)


# A generation is deliberately small; a persisted roadmap can grow across turns.
# Bound validation and transitive-closure work independently from model output.
MAX_WORKING_MILESTONES = 256


def validate_plan(plan: PlanProposal) -> None:
    validate_milestones(plan.milestones)


def validate_milestones(milestones: list[ProposedMilestone]) -> None:
    if len(milestones) > MAX_WORKING_MILESTONES:
        raise ValueError(f"工作图最多支持 {MAX_WORKING_MILESTONES} 个交付里程碑，请拆分项目")
    nodes = {m.id: m for m in milestones}
    if len(nodes) != len(milestones):
        raise ValueError("里程碑 ID 必须唯一")
    keys = [b.key for m in milestones for b in m.behaviors]
    if len(keys) != len(set(keys)):
        raise ValueError("同一计划中的行为 key 只能属于一个里程碑")
    visiting, visited = set(), set()

    def visit(mid):
        if mid in visiting:
            raise ValueError("依赖图中存在环")
        if mid in visited:
            return
        visiting.add(mid)
        for dep in nodes[mid].dependencies:
            if dep not in nodes:
                raise ValueError(f"依赖 {dep} 不存在")
            if not nodes[mid].dependency_reasons.get(dep, "").strip():
                raise ValueError(f"{mid} → {dep} 缺少依赖理由")
            visit(dep)
        visiting.remove(mid)
        visited.add(mid)

    for mid in nodes:
        node = nodes[mid]
        if len(node.dependencies) != len(set(node.dependencies)):
            raise ValueError("同一前置依赖不能重复")
        if set(node.dependency_reasons) - set(node.dependencies):
            raise ValueError("依赖理由引用了不存在的连线")
        if set(nodes[mid].change_types) - CHANGE_POLICIES.keys():
            raise ValueError("未知变更类别，请使用提供的 change_types")
        visit(mid)


def current_evidence(project: Project, behavior_id: str):
    baseline = project.baseline
    if baseline is None or not baseline.complete:
        return None
    active = {m.id: m for m in project.milestones}
    matching = [
        e
        for e in project.evidence
        if behavior_id in e.behavior_revision_ids
        and e.fingerprint == baseline.fingerprint
        and e.baseline_id == baseline.id
        and e.milestone_id in active
        and behavior_id in active[e.milestone_id].behavior_revision_ids
        and e.architecture_revision == active[e.milestone_id].architecture_revision
    ]
    return matching[-1] if matching and matching[-1].result == "PASS" else None


def current_source(project: Project, milestone):
    """Source inference proves presence in the current baseline, not acceptance."""
    return (
        milestone.origin == "source"
        and project.baseline is not None
        and project.baseline.complete
        and milestone.source_baseline_id == project.baseline.id
        and bool(milestone.source_behaviors)
    )


def readiness(project: Project, mid: str) -> dict:
    from .dependencies import ancestor_sets

    m = project.milestone(mid)
    blockers = []
    # After visual reduction the complete prerequisite chain still gates execution.
    nodes = [*project.source_milestones, *project.milestones]
    graph = {node.id: node.dependencies for node in nodes}
    for dep in sorted(ancestor_sets(graph)[mid]):
        predecessor = next(node for node in nodes if node.id == dep)
        if predecessor.origin == "source" and not current_source(project, predecessor):
            blockers.append(f"等待 {dep} 的当前源码基线")
        elif predecessor.origin != "source" and not all(
            current_evidence(project, b) for b in predecessor.behavior_revision_ids
        ):
            blockers.append(f"等待 {dep} 的当前基线验收")
    if any(not o.resolved for o in m.obligations):
        blockers.append("调查义务尚未完成")
    logical_ready = not blockers
    if project.baseline is None or not project.baseline.complete:
        blockers.append("需要完整的仓库基线")
    conflicts = [
        other.id
        for other in project.milestones
        if other.id != mid
        and other.lease_active
        and (not m.resources or not other.resources or set(m.resources) & set(other.resources))
    ]
    if conflicts:
        blockers.append("资源冲突：" + "、".join(conflicts))
    return {
        "logical_ready": logical_ready,
        "safe_to_execute": not blockers,
        "blockers": blockers,
        "conflicts": conflicts,
    }


def acceptance(project: Project) -> dict:
    required = project.targets[-1].required_behavior_ids if project.targets else []
    passed = sum(current_evidence(project, b) is not None for b in required)
    return {
        "passed": passed,
        "total": len(required),
        "achieved": bool(required) and passed == len(required),
    }
