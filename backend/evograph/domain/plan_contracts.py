"""Source-anchored planning contracts. Links enforce traceability, never entailment."""
import hashlib
import json
from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class IntentSource(ContractModel):
    id: str
    text: str
    origin: Literal["user", "legacy"] = "user"
    message_id: str = ""
    reference_context: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    activity: list[dict] = Field(default_factory=list)

class ProcessConstraint(ContractModel):
    """An instruction about its source request, never a product acceptance item."""
    id: str
    source_id: str
    quote: str
    rule: Literal["planning_only", "no_external_research", "other"]

class Retirement(ContractModel):
    source_id: str
    quote: str
    reason: str

class Requirement(ContractModel):
    id: str
    quote: str
    source_id: str
    origin: Literal["user", "legacy"] = "user"
    kind: Literal["outcome", "constraint", "exclusion"] = "outcome"
    active: bool = True
    retired_by: Retirement | None = None

class TypedContractModel(ContractModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("typed capability fields must not be blank")
        return value

class ProvidedCapability(TypedContractModel):
    key: str = Field(min_length=1, max_length=80)
    kind: Literal["command", "query"]
    action: str = Field(min_length=1, max_length=80)

class InvokeCapabilityStep(TypedContractModel):
    kind: Literal["invoke_command", "invoke_query"]
    capability_key: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=80)

class InspectAcceptanceStep(TypedContractModel):
    kind: Literal["inspect"]
    requirement_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=80)

AcceptanceStep = Annotated[
    InvokeCapabilityStep | InspectAcceptanceStep, Field(discriminator="kind")
]

class ContractBinding(ContractModel):
    behavior_key: str
    behavior_revision_id: str
    requirement_ids: list[str]
    component_ids: list[str] = Field(default_factory=list)
    mechanism: str
    requires_behavior_keys: list[str] = Field(default_factory=list)
    provides: list[ProvidedCapability] = Field(default_factory=list, max_length=16)
    # None is legacy/unannotated; [] explicitly clears typing and is also unknown.
    steps: list[AcceptanceStep] | None = Field(default=None, max_length=24)

class PlanContract(ContractModel):
    sources: list[IntentSource] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    bindings: list[ContractBinding] = Field(default_factory=list)
    process_constraints: list[ProcessConstraint] = Field(default_factory=list)

# Only these fields can cross candidate -> canonical. Execution evidence,
# baseline identities, settings and repository identity are not writable.
PLANNING_FIELDS = (
    "target_draft", "targets", "plans", "milestones", "behaviors", "proposal",
    "proposal_revision", "diagrams", "architectures", "source_milestones",
    "source_analysis_baseline_id", "source_analysis_summary", "research", "plan_contract",
)

def planning_payload(project):
    return project.model_dump(include=set(PLANNING_FIELDS))

def candidate_hash(project):
    return hashlib.sha256(json.dumps(planning_payload(project), ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def planning_fingerprint(project):
    """Detect a real repair, not the start/finish audit of a no-op tool.

    Publication/review identity still uses candidate_hash including activity.
    Sources, evidence, constraints and the actual plan remain in this fingerprint.
    """
    payload = planning_payload(project)
    for source in payload["plan_contract"]["sources"]:
        source.pop("activity", None)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def active_behaviors(project):
    ids = {bid for m in project.milestones for bid in m.behavior_revision_ids}
    return {b.behavior_key: b for b in project.behaviors if b.id in ids}

def bootstrap_contract(project):
    """Inherited contracts are explicitly not invented user quotations."""
    if project.plan_contract.sources or project.plan_contract.requirements:
        return
    contract = project.plan_contract
    if project.targets:
        source = IntentSource(id="legacy-target", text=project.targets[-1].statement, origin="legacy")
        contract.sources.append(source)
        contract.requirements.append(Requirement(id=source.id, source_id=source.id,
            quote=source.text, origin="legacy"))
    for behavior in active_behaviors(project).values():
        if behavior.acceptance_scope != "target":
            continue
        source = IntentSource(id="legacy-" + behavior.id, text=behavior.statement, origin="legacy")
        contract.sources.append(source)
        contract.requirements.append(Requirement(id=source.id, source_id=source.id,
            quote=source.text, origin="legacy"))

def contract_findings(project, *, include_availability=True):
    """Exact coverage and declared slice availability, no NLP truth heuristic."""
    findings = []
    def issue(code, subject, message):
        findings.append({"code": code, "subject": subject, "message": message})
    contract = project.plan_contract
    sources = {s.id: s for s in contract.sources}
    requirements = {r.id: r for r in contract.requirements}
    if len(requirements) != len(contract.requirements):
        issue("duplicate_requirement", "requirements", "需求身份重复")
    active = {r.id: r for r in contract.requirements if r.active}
    if project.milestones and not active:
        issue("missing_intent", "requirements", "尚未记录目标的需求依据")
    for r in contract.requirements:
        source = sources.get(r.source_id)
        if not source or not r.quote.strip() or r.quote not in source.text:
            issue("invalid_source_anchor", r.id, "需求原话不在保存的来源中")
        if not r.active:
            retirement = r.retired_by
            source = sources.get(retirement.source_id) if retirement else None
            if not retirement or not source or source.origin != "user" or retirement.quote not in source.text:
                issue("invalid_retirement", r.id, "撤销需求缺少本轮用户原话与理由")
    process_ids = set()
    for constraint in contract.process_constraints:
        identity = (constraint.source_id, constraint.id)
        if identity in process_ids:
            issue("duplicate_process_constraint", constraint.id, "同一请求的过程约束身份重复")
        process_ids.add(identity)
        source = sources.get(constraint.source_id)
        if not source or source.origin != "user" or not constraint.quote.strip() or constraint.quote not in source.text:
            issue("invalid_process_anchor", constraint.id, "过程约束缺少对应请求的精确原话")
            continue
        if constraint.rule == "no_external_research" and any(
            action.get("name") in {"web_search", "web_fetch"}
            for action in [*source.activity, *source.evidence]
        ):
            issue("process_constraint_violated", constraint.id, "该请求要求不联网调查，但已有外部调查尝试")
    behaviors = active_behaviors(project)
    owners = {m.id: m for m in project.milestones}
    components = {n.id for n in project.architectures[-1].diagram.nodes} if project.architectures else set()
    by_key, covered = {}, set()
    for binding in contract.bindings:
        key = binding.behavior_key
        if key in by_key:
            issue("duplicate_binding", key, "同一验收有重复契约映射")
        by_key[key] = binding
        behavior = behaviors.get(key)
        if not behavior:
            issue("inactive_binding", key, "契约引用了非活跃验收")
            continue
        if behavior.id != binding.behavior_revision_id:
            issue("stale_binding", key, "验收已修订，机制与需求关联需要重新核对")
        if not binding.requirement_ids or set(binding.requirement_ids) - active.keys():
            issue("invalid_requirement_link", key, "验收必须关联仍有效的需求依据")
        if behavior.acceptance_scope == "target":
            covered.update(binding.requirement_ids)
        if not binding.mechanism.strip():
            issue("missing_mechanism", key, "尚未说明可实现此验收的机制")
        if set(binding.component_ids) - components:
            issue("missing_component", key, "机制引用了不存在的架构组件")
        if set(binding.component_ids) - set(owners[behavior.owner].architecture_components):
            issue("unmapped_mechanism", key, "契约组件未与交付切片关联")
    for key in behaviors.keys() - by_key.keys():
        issue("untraced_acceptance", key, "活跃验收尚未关联需求与实现机制")
    for rid in active.keys() - covered:
        issue("uncovered_requirement", rid, "有效需求尚未由最终目标验收覆盖")
    if include_availability:
        findings.extend(declared_availability_findings(project))
    return findings


def declared_availability_findings(project):
    """Only declared owner/prerequisite reachability, never mechanism entailment."""
    from .dependencies import ancestor_sets

    behaviors = active_behaviors(project)
    ancestors = ancestor_sets({m.id: m.dependencies
                               for m in [*project.source_milestones, *project.milestones]})
    findings = []
    for binding in project.plan_contract.bindings:
        behavior = behaviors.get(binding.behavior_key)
        if behavior is None:
            continue  # The contract-integrity check owns inactive bindings.
        available = {*ancestors.get(behavior.owner, set()), behavior.owner}
        for needed in binding.requires_behavior_keys:
            dependency = behaviors.get(needed)
            if not dependency or dependency.owner not in available:
                findings.append({"code": "future_control", "subject": binding.behavior_key,
                                 "message": f"所需契约 {needed} 不在此切片或已声明前置中"})
    return findings


def review_subjects(project):
    return ["requirement:" + r.id for r in project.plan_contract.requirements if r.active] + [
        "retirement:" + r.id for r in project.plan_contract.requirements if not r.active
    ] + ["process:" + c.source_id + ":" + c.id for c in project.plan_contract.process_constraints] + [
        "goal_preservation", "cross_contract_consistency", "failure_boundaries", "cohesion_and_scope"
    ] + ["slice_activation:" + m.id for m in project.milestones]

def eligible_execution_evidence(project):
    """Return copies of existing records applicable to this exact project state.

    Eligibility checks identity and current context, not whether record contents
    are truthful, whether a model conclusion follows, or whether anything ran in
    this planning turn. PASS, FAIL and ERROR records are equally referenceable;
    an older PASS is not selected in preference to a newer failure. Every pinned
    behavior must still be active under the record's original owner.
    """
    baseline = project.baseline
    if (baseline is None or not baseline.complete or not baseline.id.strip()
            or not baseline.fingerprint.strip()
            or sum(b.id == baseline.id for b in project.baselines) != 1):
        return []
    owner_counts = Counter(m.id for m in project.milestones)
    owners = {m.id: m for m in project.milestones if owner_counts[m.id] == 1}
    revision_counts = Counter(b.id for b in project.behaviors)
    revisions = {b.id: b for b in project.behaviors if revision_counts[b.id] == 1}
    active_counts = Counter(bid for m in project.milestones for bid in m.behavior_revision_ids)
    evidence_counts = Counter(e.id for e in project.evidence)
    result = []
    for evidence in project.evidence:
        owner = owners.get(evidence.milestone_id)
        ids = evidence.behavior_revision_ids
        if (not evidence.id.strip() or evidence_counts[evidence.id] != 1
                or evidence.result not in {"PASS", "FAIL", "ERROR"}
                or owner is None or owner.origin != "plan"
                or evidence.baseline_id != baseline.id or evidence.fingerprint != baseline.fingerprint
                or evidence.architecture_revision != owner.architecture_revision
                or not ids or len(ids) != len(set(ids))):
            continue
        if any(not bid.strip() or bid not in revisions or revisions[bid].owner != owner.id
               or bid not in owner.behavior_revision_ids or active_counts[bid] != 1 for bid in ids):
            continue
        result.append({"project_id": project.id, **evidence.model_dump()})
    return result


def _validate_execution_basis(basis, ids):
    if basis == "existing_execution_record":
        if not ids:
            raise ValueError("引用既有执行记录必须提供 execution_evidence_ids")
        if len(ids) != len(set(ids)):
            raise ValueError("execution_evidence_ids 不能重复")
    elif basis in {"model_inference", "source_statement"}:
        if ids:
            raise ValueError("仅 existing_execution_record 可以携带 execution_evidence_ids")
    else:
        raise ValueError("规划评审不能声明本轮执行验证或使用未知依据类型")


class SemanticCheck(ContractModel):
    subject: str
    verdict: Literal["supported", "contradicted", "unknown"]
    reason: str = Field(min_length=1, max_length=3000)
    counterexample: str = Field(min_length=1, max_length=2000)
    basis: Literal["model_inference", "source_statement", "existing_execution_record"] = Field(
        default="model_inference",
        description="Model inference, source wording, or an allowed pre-existing execution record. "
        "No code or implementation validation is executed by this planning review.",
    )
    execution_evidence_ids: list[str] = Field(
        default_factory=list, max_length=100,
        description="Only IDs in available_execution_evidence; required for existing_execution_record, "
        "empty for other bases. A reference does not prove record contents or new execution.",
    )

    @model_validator(mode="after")
    def validate_basis(self):
        _validate_execution_basis(self.basis, self.execution_evidence_ids)
        return self

class SemanticReview(ContractModel):
    candidate_hash: str
    summary: str = Field(min_length=1, max_length=2000)
    checks: list[SemanticCheck] = Field(min_length=1, max_length=300)

def validate_semantic_review(project, review):
    if review.candidate_hash != candidate_hash(project):
        raise ValueError("评审引用了过期候选版本")
    subjects = [c.subject for c in review.checks]
    if len(set(subjects)) != len(subjects) or set(subjects) != set(review_subjects(project)):
        raise ValueError("评审未逐项覆盖准确的需求、撤销和整体一致性检查")
    eligible_ids = {e["id"] for e in eligible_execution_evidence(project)}
    for check in review.checks:
        # Recheck mutable model instances too, rather than relying exclusively on
        # initial schema parsing before a caller may have edited their fields.
        _validate_execution_basis(check.basis, check.execution_evidence_ids)
        if set(check.execution_evidence_ids) - eligible_ids:
            raise ValueError("执行依据不存在于本项目当前有效验收记录中，或基线、行为、归属、架构版本已过期")
    return all(c.verdict == "supported" for c in review.checks)


def history_findings(before, candidate):
    """Protect exact source/anchor history independently of the generator/tool path."""
    findings = []
    current_sources = {s.id: s for s in candidate.plan_contract.sources}
    current_requirements = {r.id: r for r in candidate.plan_contract.requirements}
    process = {(c.source_id, c.id): c for c in candidate.plan_contract.process_constraints}
    for constraint in before.plan_contract.process_constraints:
        if process.get((constraint.source_id, constraint.id)) != constraint:
            findings.append({"code": "process_history_changed", "subject": constraint.id,
                             "message": "既有请求的过程约束记录不可覆盖或移除"})
    for source in before.plan_contract.sources:
        if current_sources.get(source.id) != source:
            findings.append({"code": "source_history_changed", "subject": source.id, "message": "既有用户来源不可覆盖或移除"})
    for requirement in before.plan_contract.requirements:
        current = current_requirements.get(requirement.id)
        fields = {"id", "quote", "source_id", "origin", "kind"}
        if not current or current.model_dump(include=fields) != requirement.model_dump(include=fields):
            findings.append({"code": "requirement_history_changed", "subject": requirement.id, "message": "既有需求依据不可覆盖或移除"})
        elif not requirement.active and current != requirement:
            findings.append({"code": "retirement_history_changed", "subject": requirement.id, "message": "历史撤销记录不可覆盖；重新启用请使用新的来源身份"})
    return findings
