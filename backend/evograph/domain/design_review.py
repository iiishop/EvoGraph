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
            "补充当前用户确认的目标完成时必须成立的行为，并标记为 target；不要把临时要求或已推迟的功能自动纳入目标",
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
                "交付与当前架构待关联",
                "仅关联实际影响的组件；暂无合适组件可保留待关联，不得覆盖用户本轮不做架构的范围",
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


@rule
def retirement_traceability(project):
    owners = {m.id: m for m in project.milestones}
    for milestone in project.milestones:
        for step in milestone.migration_steps:
            if step.milestone_id != milestone.id:
                yield finding(
                    "retirement_owner_mismatch",
                    milestone.id,
                    f"组件 {step.component_id} 的迁移步骤记录了其他里程碑归属",
                    "恢复迁移步骤与实际所属里程碑的一致性",
                    "error",
                )
    if not project.architectures:
        return
    for revision in project.architectures:
        for step in revision.retirements:
            if step.milestone_id not in owners:
                # Absence alone cannot distinguish abandoned, unfinished or
                # completed work. Keep normal plan evolution possible and surface
                # the historical reference for evidence-based review instead.
                yield finding(
                    "retirement_owner_missing",
                    step.component_id,
                    f"A{revision.number} 退役记录的里程碑 {step.milestone_id} 不在当前路线图中",
                    "核对历史中该退役是否已完成或明确取消；若仍需执行则恢复迁移归属，不推断完成状态",
                )
    architecture = project.architectures[-1]
    for step in architecture.retirements:
        owner = owners.get(step.milestone_id)
        if owner is not None and owner.status != "VERIFIED_COMPLETE" and not any(
            saved.component_id == step.component_id
            and saved.milestone_id == owner.id
            and saved.from_revision == architecture.number - 1
            for saved in owner.migration_steps
        ):
            yield finding(
                "retirement_step_missing",
                owner.id,
                f"组件 {step.component_id} 的退役记录未保存为对应迁移步骤",
                "恢复该架构版本对应的迁移步骤，保留既有行为契约和历史记录",
                "error",
            )


SEMANTIC_REVIEW = [
    "当前用户确认的目标（包括排除项）是否由 target 行为覆盖？已推迟的功能是否仍在范围外？局部或过渡验收是否标为 milestone，并仍保留在本步契约中？",
    "标成可选、可跳过或叶子的工作是否仍有 target 行为进入最终目标？是否新增了用户没有要求、当前也非必要的产品能力？以 prospective_target_membership 的实际计数核对，不能只看文字标签。",
    "是否有无关或重复工作？最终目标与每一步的验收标准是否各自清楚？",
    "每个里程碑能否独立合并、说明价值，并让外部验收者按触发条件验证结果？",
    "每一步消费的数据结构、认证和配置是否已由前置步骤提供，或包含在本步交付中？可合并、可演示与可供真实用户上线是否被混淆？",
    "每条前置边是否确实阻塞交付，而非技术关联或人为串行？",
    "架构职责、接口、数据所有权与信任边界是否一致，关键失败路径是否可恢复？",
    "外部副作用的超时是否可能已经成功？本地事务、去重行和锁是否被误当成远端只执行一次的证明？崩溃、领取过期、取消并发和备份重放如何处理？",
    "目标、组件契约、质量场景、行为、风险和最终说明是否相互矛盾？是否未经用户选择就降低了明确的可靠性或不重复要求，或缩小历史、时间窗口、失败场景来迁就方案？披露例外或称为默认选择不等于用户同意。",
    "技术选择是否有项目约束和真实证据支撑，简单方案为何不足？",
    "迫使增加表、层或服务的技术限制是否已核实？抓取失败或无关搜索不构成证据；未核实前提是否在决策和最终回复中仍被当成事实？",
    "迁移中的各个中间状态是否可运行，兼容、回滚与退役是否有明确步骤？",
    "旧/新读写版本共存时如何补齐数据并启用约束？旧的未隔离读取是否会接触新租户数据，回滚是否会丢掉数据或安全边界？恢复目标是否有可兼容的备份材料与实际恢复流程支撑？",
    "SRC 事实、设计建议、待验证假设是否明确区分，类图和辅助图是否同步？",
]


def review_design(project: Project):
    findings = [item for check in RULES for item in check(project)]
    behaviors = {b.id: b for b in project.behaviors}
    membership = []
    for milestone in project.milestones:
        active = [behaviors[bid] for bid in milestone.behavior_revision_ids if bid in behaviors]
        target_count = sum(b.acceptance_scope == "target" for b in active)
        membership.append({
            "milestone_id": milestone.id,
            "title": milestone.title,
            "target_behavior_count": target_count,
            "milestone_behavior_count": len(active) - target_count,
            "missing_behavior_count": len(milestone.behavior_revision_ids) - len(active),
        })
    return {
        "project_revision": project.revision,
        "status": "needs_review" if findings else "structural_checks_clear",
        "findings": findings,
        "semantic_review": SEMANTIC_REVIEW,
        "prospective_target_membership": {
            "basis": "当前活跃行为的 acceptance_scope；本轮收尾据此生成当前用户确认目标的最终契约，已推迟的功能除非用户加入否则仍在范围外；不依据叶子节点或可选文字标签。",
            "target_behavior_count": sum(row["target_behavior_count"] for row in membership),
            "milestones": membership,
        },
        "limitation": "结构检查不证明设计正确，不代表验收通过；review 级提示需结合证据判断适用性。",
    }
