"""Compile one bounded, linked planning patch before checkpointing its candidate.

Only the private graph editor defers intermediate topology validation. Existing
services still own behavior revisions, restorations, architecture migration and
contract revisions; the complete result must pass their final invariants. The
pending architecture view supplies component availability, never a stored fake
revision. No compilation write reaches the candidate or canonical database.
"""
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Literal

from pydantic import Field

from ..agent_tools.base import ToolContext, ToolSpec
from ..domain.design_review import review_design
from ..domain.models import (
    ArchitectureRevision,
    ArchitectureSpec,
    Model,
    ProposedBehavior,
    ProposedMilestone,
)
from ..domain.plan_contracts import (
    PLANNING_FIELDS,
    active_behaviors,
    candidate_hash,
    contract_findings,
    history_findings,
    planning_payload,
)
from ..domain.target_contract import target_finalization
from ..infrastructure.database import ConflictError
from .design import DesignService
from .graph_editor import GraphEditor
from .plan_contracts import (
    AddProcessConstraint,
    AddRequirement,
    BindContract,
    RetireRequirement,
    TracePlan,
    trace_plan,
)


class PatchBehavior(ProposedBehavior):
    statement: str = Field(min_length=1, max_length=1000)
    acceptance_scope: Literal["target", "milestone"] = Field(
        default="target", description="Final goal or local check. Omit to retain existing scope.",
    )
    requirement_ids: list[str] = Field(min_length=1, max_length=100)
    mechanism: str = Field(min_length=1, max_length=2500)
    component_ids: list[str] = Field(
        default_factory=list, max_length=40,
        description="Concrete architecture owners. Omit on update to retain the existing binding.",
    )
    requires_behavior_keys: list[str] = Field(
        default_factory=list, max_length=100,
        description="Contracts required before this acceptance holds. Omit to retain existing links.",
    )


class PatchMilestone(ProposedMilestone):
    dependencies: list[str] = Field(default_factory=list, max_length=24)
    dependency_reasons: dict[str, str] = Field(default_factory=dict)
    behaviors: list[PatchBehavior] = Field(min_length=1, max_length=12)
    restore_inactive_behavior_keys: list[str] = Field(
        default_factory=list, max_length=12,
        description="Explicitly restore historical inactive keys only when current user intent supports it.",
    )


class PlanPatch(Model):
    summary: str = Field(default="更新统一规划候选", min_length=1, max_length=2000)
    add_requirements: list[AddRequirement] = Field(default_factory=list, max_length=40)
    retire_requirements: list[RetireRequirement] = Field(default_factory=list, max_length=40)
    process_constraints: list[AddProcessConstraint] = Field(default_factory=list, max_length=20)
    target: str | None = Field(default=None, min_length=1, max_length=4000)
    architecture: ArchitectureSpec | None = None
    milestones: list[PatchMilestone] = Field(default_factory=list, max_length=24)
    remove_milestone_ids: list[str] = Field(default_factory=list, max_length=24)


class _MemoryDatabase:
    """A transaction-local service adapter with no durable write capability."""
    is_candidate = True

    def __init__(self, staged, project):
        self.project = project.model_copy(deep=True)
        self.canonical = staged.canonical
        self.source_id = staged.source_id
        self.requirement_source_ids = set(staged.requirement_source_ids)
        self.protected = project.model_dump(exclude=set(PLANNING_FIELDS))

    def get(self, project_id):
        if project_id != self.project.id:
            raise ValueError("候选不能访问其他项目")
        return self.project.model_copy(deep=True)

    def save(self, project, kind, detail=""):
        if project.id != self.project.id or project.revision != self.project.revision:
            raise ConflictError("候选版本已改变")
        if project.model_dump(exclude=set(PLANNING_FIELDS)) != self.protected:
            raise ValueError("此操作超出规划候选的可写范围")
        self.project = project.model_copy(deep=True)
        return project


class _PendingArchitectureDatabase:
    """Resolve upsert component IDs against the declared future architecture.

    DesignService later validates/applies the real spec against the original
    architecture history and completed milestone set. Its no-op version remains
    correct because this view uses the existing architecture revision number.
    """
    def __init__(self, memory, architecture):
        self.memory, self.architecture = memory, architecture

    def get(self, project_id):
        project = self.memory.get(project_id)
        if self.architecture is not None:
            number = project.architectures[-1].number if project.architectures else 0
            # Include retiring IDs only in this availability view: legacy omitted
            # mappings survive upsert, then DesignService removes retired mappings.
            preview = ArchitectureRevision(**self.architecture.model_dump(), number=number)
            current = project.architectures[-1] if project.architectures else None
            existing = {n.id for n in preview.diagram.nodes}
            if current:
                preview.diagram.nodes.extend(n.model_copy(deep=True) for n in current.diagram.nodes
                                             if n.id not in existing)
            project.architectures.append(preview)
        return project

    def save(self, project, kind, detail=""):
        if self.architecture is not None:
            project.architectures = self.memory.project.model_copy(deep=True).architectures
        return self.memory.save(project, kind, detail)


class _CompilingGraphEditor(GraphEditor):
    def _reusable_behavior_revision(self, active, latest):
        # Reconstructing an owner must not revive a newer inactive variant or
        # revise a wholly unchanged active contract. Real edits still append to
        # the latest history in GraphEditor.upsert.
        return active or latest

    def _save(self, project, summary, before):
        # Do not validate partial forward references, add planning revisions or
        # invalidate positions until the complete graph exists in private memory.
        return self.db.save(project, "compiling_graph", summary)


_BINDING_FIELDS = {"requirement_ids", "mechanism", "component_ids", "requires_behavior_keys"}
_CONTRACT_FIELDS = ("owner", "statement", "acceptance_scope", "requirement_ids", "mechanism",
                    "component_ids", "requires_behavior_keys")


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def compiled_plan_hash(patch):
    return _json_hash(patch.model_dump(mode="json", exclude_unset=True))


def contract_changes(before, after):
    """Compare actual active contracts, never model-authored change claims.

    Revision IDs and owners are explicit even for binding-only edits. Null in a
    diff means an absent side of an add/remove, not an accepted patch value.
    """
    def snapshot(project):
        bindings = {item.behavior_key: item for item in project.plan_contract.bindings}
        result = {}
        for key, behavior in active_behaviors(project).items():
            fields = behavior.model_dump(include={"owner", "statement", "acceptance_scope"})
            if key in bindings:
                fields.update(bindings[key].model_dump(include=_BINDING_FIELDS))
            result[key] = (behavior.id, fields)
        return result

    old, new = snapshot(before), snapshot(after)
    changes = []
    for key in sorted(old.keys() | new.keys()):
        previous_id, previous = old.get(key, (None, {}))
        current_id, current = new.get(key, (None, {}))
        fields = {field: {"before": previous.get(field), "after": current.get(field)}
                  for field in _CONTRACT_FIELDS if previous.get(field) != current.get(field)}
        if not fields and previous_id == current_id:
            continue
        changes.append({
            "key": key, "operation": "added" if key not in old else "removed" if key not in new else "updated",
            "before_owner": previous.get("owner"), "after_owner": current.get("owner"),
            "before_revision_id": previous_id, "after_revision_id": current_id, "fields": fields,
        })
    return changes


def _check_compiler_base(audit, before, args):
    if audit is None or audit.get("protocol_version") == "plan-delta/v1":
        return
    if (audit.get("protocol_version") != "plan-delta/v2"
            or audit.get("project_id") != before.id
            or audit.get("base_revision") != before.revision
            or audit.get("base_candidate_hash") != candidate_hash(before)
            or audit.get("compiled_hash") != compiled_plan_hash(args)):
        raise ValueError("compiled delta does not match the exact candidate revision/hash")


def _completed_compiler_audit(audit, before, after, *, changed):
    if audit is None or audit.get("protocol_version") == "plan-delta/v1":
        return audit
    result = deepcopy(audit)
    result.pop("audit_hash", None)
    result["result_revision"] = before.revision + int(changed)
    result["result_candidate_hash"] = candidate_hash(after)
    result["contract_changes"] = contract_changes(before, after)
    reasons = {item["key"]: item["owner_change_reason"]
               for item in audit["ir"].get("contracts", []) if "owner_change_reason" in item}
    for change in result["contract_changes"]:
        if change["key"] in reasons and "owner" in change["fields"]:
            change["owner_change_reason"] = reasons[change["key"]]
    result["audit_hash"] = _json_hash(result)
    return result


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(label + "不能重复")


def _prepare_graph(memory, args):
    """Validate final ownership, then detach only explicitly vacated references.

    Removal's lease protection is identical to GraphEditor.remove. Its dependent
    guard is deferred to final graph validation, allowing explicit dependent edits
    and dependent-group removals in either input order without pruning any edge.
    """
    project = memory.get(memory.project.id)
    existing = {m.id: m for m in project.milestones}
    upserts = {m.id: m for m in args.milestones}
    removed = set(args.remove_milestone_ids)
    _unique([m.id for m in args.milestones], "里程碑 ID ")
    _unique(args.remove_milestone_ids, "移除里程碑 ID ")
    if removed & upserts.keys():
        raise ValueError("同一里程碑不能同时更新和移除")
    if removed - existing.keys():
        raise ValueError("移除的里程碑不存在：" + ", ".join(sorted(removed - existing.keys())))
    for mid in removed | upserts.keys():
        if mid in existing and existing[mid].lease_active:
            raise ValueError("该节点已领取，请先释放后修改")
    prior_active = active_behaviors(project)
    desired = {}
    for node in project.milestones:
        if node.id not in removed and node.id not in upserts:
            for key, behavior in prior_active.items():
                if behavior.owner == node.id:
                    desired[key] = node.id
    for node in args.milestones:
        for behavior in node.behaviors:
            if behavior.key in desired:
                raise ValueError("同一计划中的行为 key 只能属于一个里程碑：" + behavior.key)
            desired[behavior.key] = node.id
    project.milestones = [m for m in project.milestones if m.id not in removed]
    for node in project.milestones:
        moved = {b.id for key, b in prior_active.items()
                 if b.owner == node.id and desired.get(key, node.id) != node.id}
        node.behavior_revision_ids = [bid for bid in node.behavior_revision_ids if bid not in moved]
    memory.save(project, "compiling_removals")
    return prior_active


def propose_plan_patch(ctx, args, *, compiler_audit=None):
    db = ctx.application.db
    if not getattr(db, "is_candidate", False):
        raise ValueError("统一规划补丁只能在候选规划中修改")
    before = db.get(ctx.project_id)
    _check_compiler_base(compiler_audit, before, args)
    if before.archived:
        raise ValueError("项目已删除")
    _unique([r.id for r in args.add_requirements], "新增需求 ID ")
    _unique([r.id for r in args.retire_requirements], "撤销需求 ID ")
    memory = _MemoryDatabase(db, before)
    prior_active = _prepare_graph(memory, args)
    graph = _CompilingGraphEditor(_PendingArchitectureDatabase(memory, args.architecture))
    existing_bindings = {b.behavior_key: b for b in before.plan_contract.bindings}
    bindings, restored = [], []
    for item in args.milestones:
        payload = item.model_dump(exclude_unset=True, exclude={"behaviors", "restore_inactive_behavior_keys"})
        payload["behaviors"] = [b.model_dump(exclude_unset=True, exclude=_BINDING_FIELDS)
                                for b in item.behaviors]
        for behavior, proposed in zip(item.behaviors, payload["behaviors"]):
            prior = prior_active.get(behavior.key)
            if prior and prior.owner != item.id and "acceptance_scope" not in behavior.model_fields_set:
                # An active move retains its active scope, even when a newer
                # unused historical revision of that key has another scope.
                proposed["acceptance_scope"] = prior.acceptance_scope
        for behavior in item.behaviors:
            links = behavior.model_dump(include=_BINDING_FIELDS)
            previous = existing_bindings.get(behavior.key)
            if previous:
                for name in ("component_ids", "requires_behavior_keys"):
                    if name not in behavior.model_fields_set:
                        links[name] = list(getattr(previous, name))
            bindings.append(BindContract(behavior_key=behavior.key, **links))
        # Direct behavior links suffice for component ownership; callers need not
        # repeat those same IDs on the milestone. Preserve additional old mappings.
        if "architecture_components" not in item.model_fields_set:
            old = next((m for m in before.milestones if m.id == item.id), None)
            mapped = list(old.architecture_components) if old else []
            mapped.extend(cid for b in bindings if b.behavior_key in {v.key for v in item.behaviors}
                          for cid in b.component_ids)
            if mapped:
                payload["architecture_components"] = list(dict.fromkeys(mapped))
        # Moving an active key within this atomic patch is not a historical
        # restoration. Retain GraphEditor restoration scope semantics internally.
        acknowledgments = list(item.restore_inactive_behavior_keys)
        _unique(acknowledgments, "restore_inactive_behavior_keys ")
        for behavior in item.behaviors:
            prior = prior_active.get(behavior.key)
            if prior and prior.owner != item.id and behavior.key not in acknowledgments:
                acknowledgments.append(behavior.key)
        result = graph.upsert(
            ctx.project_id, ProposedMilestone.model_validate(payload),
            not any(m.id == item.id for m in memory.project.milestones),
            restore_inactive_behavior_keys=acknowledgments,
        )
        restored.extend(r for r in result["restored_inactive_behaviors"]
                        if r["behavior_key"] not in prior_active)
    if args.architecture is not None:
        DesignService(memory).update(ctx.project_id, args.architecture)
    project = memory.get(ctx.project_id)
    GraphEditor(memory)._check(project)
    trace_plan(ToolContext(ctx.project_id, SimpleNamespace(db=memory)), TracePlan(
        add_requirements=args.add_requirements, retire_requirements=args.retire_requirements,
        bindings=bindings, process_constraints=args.process_constraints,
    ))
    if args.target is not None:
        GraphEditor(memory).target(ctx.project_id, args.target)
    project = memory.get(ctx.project_id)
    findings = contract_findings(project) + history_findings(before, project)
    findings.extend({k: f[k] for k in ("code", "subject", "message")}
                    for f in review_design(project)["findings"] if f["severity"] == "error")
    if findings:
        raise ValueError("规划补丁校验失败：" + "; ".join(
            f"{f['code']} [{f['subject']}]: {f['message']}" for f in findings))
    decision = target_finalization(project)
    if decision is not None and not decision.creates_version:
        project.target_draft = None
    if planning_payload(project) == planning_payload(before):
        if compiler_audit is not None:
            db.record_compilation_noop(_completed_compiler_audit(
                compiler_audit, before, project, changed=False,
            ))
        ctx.candidate_ready = True
        return {"node_ids": [], "effect": "updated", "status": "NO_PROGRESS", "findings": []}
    # One planning revision and finalization, entirely private; the outer save is
    # the only candidate checkpoint and retains optimistic concurrency protection.
    final_graph = GraphEditor(memory)
    final_graph._save(project, args.summary, [m.model_dump() for m in before.milestones])
    removed_edges = final_graph.finalize(ctx.project_id)
    project = memory.get(ctx.project_id)
    final_graph._check(project)
    findings = contract_findings(project) + history_findings(before, project)
    if findings:
        raise ValueError("规划补丁最终校验失败：" + "; ".join(f["message"] for f in findings))
    if compiler_audit is None:
        db.save(project, "plan_patch_applied", args.summary)
    else:
        db.save(project, "plan_patch_applied", args.summary, compiler_audit=_completed_compiler_audit(
            compiler_audit, before, project, changed=True,
        ))
    ctx.candidate_ready = True
    return {
        "node_ids": list(dict.fromkeys([m.id for m in args.milestones] + args.remove_milestone_ids)),
        "effect": "updated", "findings": [], "restored_inactive_behaviors": restored,
        "removed_dependencies": removed_edges,
    }


PATCH_TOOL = ToolSpec(
    "propose_plan_patch",
    "Compile one complete linked candidate patch atomically. Use stable milestone/behavior IDs; "
    "milestones are upserts (max 24), not full-graph replacement. Unmentioned nodes and requirements "
    "remain. Every submitted behavior includes requirement_ids and a concrete mechanism plus its "
    "component_ids and requires_behavior_keys; no separate trace call is needed. For updates, omitted "
    "dependencies/reasons/acceptance_scope and omitted binding component/prerequisite links retain "
    "their prior values; explicit dependency/link lists replace them. Behaviors replace this node's "
    "active set; historical restoration requires restore_inactive_behavior_keys and user support. "
    "Behavior component links automatically map the owning milestone when architecture_components "
    "is omitted. Architecture is optional and may add/retire components with migration owners in "
    "this same patch. Removal must also explicitly repair surviving prerequisite/behavior links. "
    "New requirement quotes must exactly match CURRENT user input or an explicitly named allowed "
    "pending source_id from the retained candidate. Retirement quotes must match CURRENT input; retained history is "
    "immutable. Never drop or weaken unfulfilled requirements. All final structural/trace invariants "
    "must pass before one candidate checkpoint; semantic review and canonical publication follow "
    "separately. An invalid patch changes nothing. Omit target/architecture to preserve them.",
    PlanPatch, propose_plan_patch, "更新关联完整的规划候选", "updated", None,
)
