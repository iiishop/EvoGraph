"""Patch source anchors and canonical behavior-revision bindings."""
from typing import Literal

from pydantic import Field

from ..agent_tools.base import ToolSpec
from ..domain.models import BehaviorRevision, Model
from ..domain.plan_contracts import (
    ContractBinding,
    ProcessConstraint,
    Requirement,
    Retirement,
    active_behaviors,
    contract_findings,
)


class AddRequirement(Model):
    source_id: str | None = Field(default=None, description="Omit for current input; on continuation only, an exact retained uncommitted source ID may be used.")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    quote: str = Field(min_length=1, max_length=16000)
    kind: str = Field(pattern=r"^(outcome|constraint|exclusion)$")

class RetireRequirement(Model):
    id: str
    quote: str = Field(min_length=1, max_length=16000)
    reason: str = Field(min_length=1, max_length=1500)

class AddProcessConstraint(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    quote: str = Field(min_length=1, max_length=16000)
    rule: Literal["planning_only", "no_external_research", "other"]
    source_id: str | None = None

class BindContract(Model):
    behavior_key: str
    requirement_ids: list[str] = Field(min_length=1, max_length=100)
    component_ids: list[str] = Field(default_factory=list, max_length=40)
    mechanism: str = Field(min_length=1, max_length=2500)
    requires_behavior_keys: list[str] = Field(default_factory=list, max_length=100)

class TracePlan(Model):
    add_requirements: list[AddRequirement] = Field(default_factory=list, max_length=40)
    retire_requirements: list[RetireRequirement] = Field(default_factory=list, max_length=40)
    bindings: list[BindContract] = Field(default_factory=list, max_length=256)
    process_constraints: list[AddProcessConstraint] = Field(default_factory=list, max_length=20)

def trace_plan(ctx, args):
    db = ctx.application.db
    if not getattr(db, "is_candidate", False):
        raise ValueError("统一契约只能在候选规划中修改")
    p = db.get(ctx.project_id)
    source = next(s for s in p.plan_contract.sources if s.id == db.source_id)
    by_id = {r.id: r for r in p.plan_contract.requirements}
    process = {(c.source_id, c.id): c for c in p.plan_contract.process_constraints}
    for item in args.process_constraints:
        source_id = item.source_id or source.id
        origin = next((s for s in p.plan_contract.sources if s.id == source_id), None)
        if source_id not in db.requirement_source_ids or not origin or origin.origin != "user" or item.quote not in origin.text:
            raise ValueError("过程约束必须引用本轮或当前待应用请求的精确原话")
        constraint = ProcessConstraint(**item.model_dump(exclude={"source_id"}), source_id=source_id)
        previous = process.get((source_id, item.id))
        if previous and previous != constraint:
            raise ValueError("同一请求的过程约束记录不可覆盖")
        process[(source_id, item.id)] = constraint
    p.plan_contract.process_constraints = list(process.values())
    for item in args.add_requirements:
        source_id = item.source_id or source.id
        requirement_source = next((s for s in p.plan_contract.sources if s.id == source_id), None)
        if source_id not in db.requirement_source_ids or not requirement_source or requirement_source.origin != "user":
            raise ValueError("新增需求只能引用本轮或当前保留候选中尚未应用的用户来源")
        if not item.quote.strip() or item.quote not in requirement_source.text:
            raise ValueError("新增需求的 quote 必须逐字来自当前用户输入或指定的待应用来源，不可用模型解释代替")
        existing = by_id.get(item.id)
        proposed = Requirement(**item.model_dump(exclude={"source_id"}), source_id=source_id)
        if existing and existing != proposed:
            raise ValueError("已有需求依据不可覆盖；通过 retire_requirements 保留撤销记录，再添加新身份")
        by_id[item.id] = proposed
    for item in args.retire_requirements:
        if item.id not in by_id or not item.quote.strip() or item.quote not in source.text:
            raise ValueError("撤销必须引用已有需求和本轮用户原话")
        requirement = by_id[item.id]
        if not requirement.active and requirement.retired_by != Retirement(
            source_id=source.id, quote=item.quote, reason=item.reason,
        ):
            raise ValueError("历史撤销依据不能覆盖；恢复需求请添加新的来源身份")
        if requirement.source_id == source.id:
            raise ValueError("不能在同一输入中先添加又撤销同一需求")
        requirement.active = False
        requirement.retired_by = Retirement(source_id=source.id, quote=item.quote, reason=item.reason)
    p.plan_contract.requirements = list(by_id.values())
    behaviors = active_behaviors(p)
    bindings = {b.behavior_key: b for b in p.plan_contract.bindings if b.behavior_key in behaviors}
    for item in args.bindings:
        behavior = behaviors.get(item.behavior_key)
        if not behavior:
            raise ValueError("只可关联当前活跃验收：" + item.behavior_key)
        previous = bindings.get(item.behavior_key)
        new_binding = ContractBinding(**item.model_dump(), behavior_revision_id=behavior.id)
        # Evidence pins behavior revision IDs. Mechanism/requirements form part of
        # this contract, so changing those also creates a revision and stales evidence.
        canonical_ids = {b.id for b in active_behaviors(db.canonical.get(p.id)).values()}
        if new_binding != previous and (
            behavior.id in canonical_ids
            or (previous is not None and previous.behavior_revision_id == behavior.id)
        ):
            owner = p.milestone(behavior.owner)
            if owner.lease_active:
                raise ValueError("已领取的契约不能在候选中改写，请先释放任务")
            latest = next(b for b in reversed(p.behaviors) if b.behavior_key == behavior.behavior_key)
            revised = BehaviorRevision(
                behavior_key=behavior.behavior_key, statement=behavior.statement,
                acceptance_scope=behavior.acceptance_scope, owner=behavior.owner,
                version=latest.version + 1, supersedes=latest.id,
            )
            p.behaviors.append(revised)
            owner.behavior_revision_ids = [revised.id if bid == behavior.id else bid for bid in owner.behavior_revision_ids]
            owner.status = "PLANNED"
            owner.pinned_baseline = None
            behavior = revised
            behaviors[item.behavior_key] = revised
            new_binding.behavior_revision_id = revised.id
        bindings[item.behavior_key] = new_binding
    p.plan_contract.bindings = list(bindings.values())
    db.save(p, "contract_traced", "关联需求、实现机制与验收")
    return {"node_ids": [], "effect": "updated", "findings": contract_findings(p)}

TRACE_TOOL = ToolSpec(
    "trace_plan", "Record exact user-source requirements and bind CURRENT behavior keys to them, "
    "their concrete mechanism, architecture component IDs and other behavior contracts needed BEFORE "
    "this slice can satisfy acceptance. Acceptance text is stored once in behavior revisions. "
    "Apply patches: omitted requirements/bindings remain. quote must be an exact substring of CURRENT "
    "INPUT. Existing source anchors cannot be edited. Explicit changed user direction can retire a "
    "previous requirement using its id, current quote and reason; old anchors remain auditable. "
    "Quote existence is not semantic authorization: review checks whether the requested change supports "
    "retirement. Refresh bindings after behavior edits. Each active requirement needs target acceptance; "
    "local transitional checks also need links. component_ids may be empty if architecture is deferred. "
    "Never weaken a guarantee to accommodate a mechanism; retain unresolved requirements.",
    TracePlan, trace_plan, "关联需求与交付契约", "updated", None)


class ValidateCandidate(Model):
    pass


def validate_candidate(ctx, args):
    db = ctx.application.db
    ctx.candidate_ready = True
    db.record["validation_requested"] = True
    db.record = db.store.save(db.record)
    return {"status": "VALIDATION_REQUESTED", "findings": contract_findings(db.get(ctx.project_id))}


VALIDATE_TOOL = ToolSpec(
    "validate_candidate", "Request validation of the preserved candidate when the user asks to "
    "retry/continue/finish and no planning edit is needed. This does not certify anything or waive "
    "findings. Do not call for a read-only explanation. The application will run the exact-revision "
    "checks and independent semantic challenge after your turn.",
    ValidateCandidate, validate_candidate, "重新校验保留的候选", "inspect", None,
)
