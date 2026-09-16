"""Deterministic rules. Semantic sufficiency is deliberately not a PASS claim."""

from .models import Obligation, PlanProposal, Project

# One registry is the extension point for investigation obligations.
CHANGE_POLICIES = {
    "general": [("scope", "确认变更范围与验收方式")],
    "api": [("consumers", "检查 API 消费者"), ("compatibility", "检查接口兼容性与契约")],
    "data": [("migration", "检查数据迁移与回滚"), ("data_consumers", "检查持久化数据消费者")],
    "auth": [
        ("credentials", "检查凭据存储与认证边界"),
        ("negative_paths", "检查失败路径与权限隔离"),
    ],
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


def validate_plan(plan: PlanProposal) -> None:
    nodes = {m.id: m for m in plan.milestones}
    if len(nodes) != len(plan.milestones):
        raise ValueError("里程碑 ID 必须唯一")
    keys = [b.key for m in plan.milestones for b in m.behaviors]
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
        if set(nodes[mid].change_types) - CHANGE_POLICIES.keys():
            raise ValueError("未知变更类别，请使用提供的 change_types")
        visit(mid)


def current_evidence(project: Project, behavior_id: str):
    baseline = project.baseline
    if baseline is None or not baseline.complete:
        return None
    matching = [
        e
        for e in project.evidence
        if behavior_id in e.behavior_revision_ids
        and e.fingerprint == baseline.fingerprint
        and e.baseline_id == baseline.id
    ]
    return matching[-1] if matching and matching[-1].result == "PASS" else None


def readiness(project: Project, mid: str) -> dict:
    m = project.milestone(mid)
    blockers = []
    for dep in m.dependencies:
        predecessor = project.milestone(dep)
        if not all(current_evidence(project, b) for b in predecessor.behavior_revision_ids):
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
