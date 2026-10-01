"""Deterministic design diagnostics. Add a rule in one place; never mutate the plan."""

from collections.abc import Callable

from .models import Project

RULES: list[Callable] = []


def rule(fn):
    RULES.append(fn)
    return fn


def finding(code, subject, message, action, severity="review"):
    return dict(code=code, subject=subject, severity=severity, message=message, action=action)


@rule
def milestone_contracts(project):
    behaviors = {b.id: b for b in project.behaviors}
    nodes = {m.id for m in [*project.milestones, *project.source_milestones]}
    for m in project.milestones:
        if not m.scope or not m.intent.strip() or not m.behavior_revision_ids:
            yield finding(
                "incomplete_contract",
                m.id,
                "交付范围、意图或行为契约缺失",
                "补全可独立交付的范围与可观察行为",
                "error",
            )
        for bid in m.behavior_revision_ids:
            b = behaviors.get(bid)
            if b is None or b.owner != m.id:
                yield finding(
                    "invalid_behavior",
                    m.id,
                    "行为引用不存在或属于其他里程碑",
                    "恢复正确的行为归属",
                    "error",
                )
        for dep in m.dependencies:
            if dep not in nodes:
                yield finding(
                    "missing_dependency", m.id, f"前置节点 {dep} 不存在", "修复前置引用", "error"
                )
            elif not m.dependency_reasons.get(dep, "").strip():
                yield finding(
                    "dependency_rationale",
                    m.id,
                    f"缺少对 {dep} 的依赖理由",
                    "说明必需的前置产物或移除非阻塞关联",
                )


@rule
def target_coverage(project):
    active_ids = {bid for m in project.milestones for bid in m.behavior_revision_ids}
    if active_ids and not any(
        b.id in active_ids and b.acceptance_scope == "target" for b in project.behaviors
    ):
        yield finding(
            "target_coverage_missing",
            "project",
            "当前仅有本步验收，尚未定义最终目标标准",
            "补充最终交付时仍需成立的行为，并标记为 target；不要把临时要求自动提升为最终目标",
        )


@rule
def architecture_traceability(project):
    if not project.architectures:
        if project.milestones:
            yield finding(
                "architecture_missing",
                "project",
                "里程碑尚未关联架构设计",
                "先判断变更是否涉及系统边界，必要时建立架构",
            )
        return
    a = project.architectures[-1]
    ids = {n.id for n in a.diagram.nodes}
    for m in project.milestones:
        unknown = set(m.architecture_components) - ids
        if unknown:
            yield finding(
                "unknown_component",
                m.id,
                f"引用已不存在的架构组件：{', '.join(sorted(unknown))}",
                "修复组件映射或补充迁移步骤",
                "error",
            )
        if m.status != "VERIFIED_COMPLETE" and not m.architecture_components:
            yield finding(
                "unmapped_delivery",
                m.id,
                "交付与当前架构没有组件映射",
                "关联实际影响的组件；非架构工作说明例外",
            )
        if m.status != "VERIFIED_COMPLETE" and m.architecture_revision != a.number:
            yield finding(
                "stale_architecture",
                m.id,
                "里程碑基于旧架构版本",
                "审查范围、行为和前置条件后更新里程碑",
            )
    for node in a.diagram.nodes:
        if not node.description.strip():
            yield finding(
                "component_responsibility",
                node.id,
                "组件职责描述为空",
                "说明责任、接口与数据所有权，避免只写技术名称",
            )
    for field, message in [
        ("decisions", "缺少方案取舍记录"),
        ("quality_scenarios", "缺少可衡量的质量场景"),
        ("risks", "尚未记录风险或关键假设"),
    ]:
        if not getattr(a, field):
            yield finding(
                f"architecture_{field}",
                "architecture",
                message,
                "按项目实际规模补充；不为凑齐字段编造风险或指标",
            )
    research_ids = {r.id for r in project.research}
    if set(a.research_ids) - research_ids:
        yield finding(
            "missing_research",
            "architecture",
            "架构引用的研究资料不存在",
            "重新读取来源并修正引用",
            "error",
        )


SEMANTIC_REVIEW = [
    "目标中的每项结果是否由 target 行为覆盖？临时或过渡验收是否标为 milestone，并仍保留在本步契约中？",
    "是否有无关或重复工作？最终目标与每一步的验收标准是否各自清楚？",
    "每个里程碑能否独立合并、说明价值，并让外部验收者按触发条件验证结果？",
    "每条前置边是否确实阻塞交付，而非技术关联或人为串行？",
    "架构职责、接口、数据所有权与信任边界是否一致，关键失败路径是否可恢复？",
    "技术选择是否有项目约束和真实证据支撑，简单方案为何不足？",
    "迁移中的各个中间状态是否可运行，兼容、回滚与退役是否有明确步骤？",
    "SRC 事实、设计建议、待验证假设是否明确区分，类图和辅助图是否同步？",
]


def review_design(project: Project):
    findings = [item for check in RULES for item in check(project)]
    return {
        "project_revision": project.revision,
        "status": "needs_review" if findings else "structural_checks_clear",
        "findings": findings,
        "semantic_review": SEMANTIC_REVIEW,
        "limitation": "结构检查不证明设计正确，不代表验收通过；review 级提示需结合证据判断适用性。",
    }
