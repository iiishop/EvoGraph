"""Built-in read-only checks, sealed inputs, and one runner for review and replay.

There is no dynamic loading and no provider, database, settings or mutable facade
in a plugin context. Transport is a narrow runner-owned callback; commit replay
uses its synchronous certificate validator and never calls a provider.
"""

import asyncio
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from ..domain.dependencies import ancestor_sets
from ..domain.design_review import review_design
from ..domain.graph_validation import check_graph
from ..domain.models import Project
from ..domain.plan_contracts import (
    PLANNING_FIELDS,
    SemanticReview,
    contract_findings,
    declared_availability_findings,
    history_findings,
    review_subjects,
    validate_semantic_review,
)
from ..domain.plan_harness import (
    CURRENT_POLICY,
    POLICY_HASH,
    HarnessFinding,
    HarnessRun,
    HarnessSnapshot,
    PluginExecution,
    PluginManifest,
    PluginResult,
    canonical_json,
    content_hash,
    execution_satisfied,
    policy_decision,
)
from ..domain.policies import DECLARED_SUPPORTING_CHANGE_TYPES, MAX_WORKING_MILESTONES
from ..domain.target_contract import required_target_behavior_ids
from ..domain.typed_capabilities import typed_capability_report
from .attachments import MAX_EXCERPT_CHARS
from .provider_output import ProviderOutputError

# Explicit data-only projection. Runtime metrics, credentials, request traces,
# prior reviews and mutable service handles never participate in this context.
_PROJECT_FIELDS = {*PLANNING_FIELDS, "id", "name", "description", "revision", "archived",
                   "unified_planning", "baselines", "evidence", "attachments"}
_RECORD_FIELDS = ("id", "project_id", "base_revision", "input", "reference_context", "checker_version",
                  "capability_move_audits", "typed_obligation_keys", "consumption_obligation_keys", "work_units")


def _project_data(project):
    value = project.model_dump(mode="json") if isinstance(project, Project) else dict(project)
    return {key: item for key, item in value.items() if key in _PROJECT_FIELDS}


def seal_snapshot(before, candidate, record):
    """Seal before any graph closure. Even malformed graph input gets a receipt."""
    old, new = _project_data(before), _project_data(candidate)
    context = {key: record[key] for key in _RECORD_FIELDS if key in record}
    context.setdefault("project_id", new["id"])
    context.setdefault("reference_context", {})
    payload = {
        "schema_version": "plan-harness-snapshot/v1", "candidate_id": record["id"],
        "project_id": new["id"], "candidate_hash": content_hash(
            {key: new[key] for key in PLANNING_FIELDS if key in new}),
        "before_hash": content_hash({key: old[key] for key in PLANNING_FIELDS if key in old}),
        "before_revision": old["revision"], "expected_revision": record["base_revision"],
        "candidate_revision": new["revision"], "before_json": canonical_json(old),
        "candidate_json": canonical_json(new), "record_json": canonical_json(context),
    }
    return HarnessSnapshot(snapshot_id=content_hash(payload), **payload)


def _validated_snapshot(snapshot):
    return HarnessSnapshot.model_validate_json(snapshot.model_dump_json())


def _finding(code, subject, message, *, severity="error", basis="deterministic", refs=()):
    return HarnessFinding(code=code, subject=str(subject), message=str(message),
                          severity=severity, basis=basis, evidence_refs=tuple(refs))


def _result(snapshot, plugin_id, subjects, findings=(), *, verdict=None, refs=(),
            applicability_reason=""):
    manifest = _BY_ID[plugin_id].manifest
    findings = tuple(findings)
    return PluginResult(plugin_id=plugin_id, plugin_version=manifest.version,
                        snapshot_id=snapshot.snapshot_id, scope=manifest.scope,
                        covered_subjects=tuple(dict.fromkeys(subjects)), findings=findings,
                        verdict=verdict or ("block" if any(f.severity == "error" for f in findings)
                                            else "pass"), evidence_refs=tuple(refs),
                        applicability_reason=applicability_reason)


def _identity_findings(project, *, side, finalized):
    findings = []

    def unique(values, subject):
        for value, count in Counter(values).items():
            parts = value if isinstance(value, tuple) else (value,)
            if any(not str(part).strip() for part in parts) or count > 1:
                findings.append(_finding("duplicate_or_empty_identity", f"{side}:{subject}:{value}",
                                         "输入含空白或重复身份，不能安全构造引用索引"))

    unique((m.id for m in [*project.milestones, *project.source_milestones]), "milestone")
    unique((b.id for b in project.behaviors), "behavior_revision")
    unique((s.id for s in project.plan_contract.sources), "source")
    unique((r.id for r in project.plan_contract.requirements), "requirement")
    unique((b.behavior_key for b in project.plan_contract.bindings), "binding")
    unique(((c.source_id, c.id) for c in project.plan_contract.process_constraints), "process")
    unique((a.id for a in project.attachments), "attachment")
    unique((r.number for r in project.architectures), "architecture_revision")
    unique((t.number for t in project.targets), "target_version")
    by_id = {b.id: b for b in project.behaviors}
    active_ids = [bid for m in project.milestones for bid in m.behavior_revision_ids]
    unique(active_ids, "active_behavior_revision")
    unique((by_id[bid].behavior_key for bid in active_ids if bid in by_id), "active_behavior_key")
    for node in project.milestones:
        if node.origin != "plan" or node.id.startswith("SRC_"):
            findings.append(_finding("invalid_planned_identity", node.id, "交付节点使用了源码身份"))
        for bid in node.behavior_revision_ids:
            if bid not in by_id or by_id[bid].owner != node.id:
                findings.append(_finding("invalid_behavior_owner", f"{side}:{bid}",
                                         "活跃验收引用不存在或不属于当前交付节点"))
    for node in project.source_milestones:
        if node.origin != "source" or not node.source_baseline_id:
            findings.append(_finding("invalid_source_identity", node.id, "源码能力缺少源码身份或基线"))
        unique((b.key for b in node.source_behaviors), "source_capability:" + node.id)
    for revision in project.architectures:
        unique((n.id for n in revision.diagram.nodes), "component:" + str(revision.number))
    if len(project.source_milestones) > MAX_WORKING_MILESTONES:
        findings.append(_finding("graph_size_limit", side, "源码能力图超过可校验范围"))
    if not findings:
        try:
            check_graph(project)
            # check_graph historically returns early for an empty working graph;
            # source-only malformed DAGs still cannot enter a review snapshot.
            if not project.milestones:
                ancestor_sets({m.id: m.dependencies for m in project.source_milestones})
        except (ValueError, KeyError, RecursionError) as exc:
            findings.append(_finding("invalid_graph", side, str(exc)))
    if finalized and not findings:
        expected = required_target_behavior_ids(project.milestones, project.behaviors)
        current = project.targets[-1] if project.targets else None
        if (project.target_draft is not None
                or (current is not None and current.required_behavior_ids != expected)
                or (project.milestones and current is None)):
            findings.append(_finding("unfinalized_target_membership", "candidate:target",
                                     "当前目标版本与活跃 target 验收身份不一致，需要先完成规划收尾"))
    return findings


def _graph_identity(snapshot):
    findings = []
    for side, encoded in (("before", snapshot.before_json), ("candidate", snapshot.candidate_json)):
        try:
            project = Project.model_validate_json(encoded)
        except (ValidationError, ValueError, TypeError) as exc:
            findings.append(_finding("invalid_project_schema", side, str(exc)[:2000]))
            continue
        expected_revision = (snapshot.before_revision if side == "before"
                             else snapshot.candidate_revision)
        expected_hash = snapshot.before_hash if side == "before" else snapshot.candidate_hash
        actual_hash = content_hash(project.model_dump(include=set(PLANNING_FIELDS)))
        if (project.id != snapshot.project_id or project.revision != expected_revision
                or actual_hash != expected_hash):
            findings.append(_finding("project_identity_mismatch", side, "规划项目身份、内容或快照版本不一致"))
        # Project's legacy reader removes old directory nodes. A gate must not
        # silently validate a different candidate after that compatibility step.
        if len(project.source_milestones) != len(json.loads(encoded).get("source_milestones", [])):
            findings.append(_finding("invalid_source_identity", side, "源码节点缺少有效基线身份"))
        findings.extend(_identity_findings(project, side=side, finalized=side == "candidate"))
    record = snapshot.record_data()
    if (record.get("id") != snapshot.candidate_id or record.get("project_id") != snapshot.project_id
            or record.get("base_revision") != snapshot.expected_revision
            or snapshot.before_revision > snapshot.expected_revision):
        findings.append(_finding("snapshot_identity_mismatch", "snapshot", "规划快照身份或基准版本不一致"))
    return _result(snapshot, "graph_identity", ("before", "candidate", "snapshot"), findings)


def review_source_findings(candidate):
    """Missing/full-source limitations are unresolved evidence, never a pass."""
    findings = []
    attachments_by_id = {a.id: a for a in candidate.attachments}
    for source in candidate.plan_contract.sources:
        attached = source.reference_context.get("attachments", [])
        if not isinstance(attached, list) or any(not isinstance(a, dict) for a in attached):
            findings.append(_finding("source_completeness_unknown", source.id, "附件来源记录格式不完整"))
            continue
        attachments = list(attached)
        read_ids = set()
        for entry in source.evidence:
            if entry.get("name") != "read_reference":
                continue
            arguments = entry.get("arguments")
            identity = arguments.get("attachment_id") if isinstance(arguments, dict) else None
            if not isinstance(identity, str) or not identity.strip():
                findings.append(_finding("source_completeness_unknown", source.id,
                                         "附件读取记录缺少可核对的来源身份"))
            else:
                read_ids.add(identity)
        for identity in sorted(read_ids):
            attachment = attachments_by_id.get(identity)
            if attachment is None:
                findings.append(_finding("source_completeness_unknown", source.id,
                                         "已读取附件的完整来源记录不可用"))
            else:
                attachments.append({"id": attachment.id, "name": attachment.name,
                                    "text": attachment.excerpt, "media_type": attachment.media_type})
        for attachment in attachments:
            name = str(attachment.get("name", attachment.get("id", "未知附件")))
            if str(attachment.get("media_type", "")).startswith("image/"):
                findings.append(_finding("source_completeness_unknown", source.id,
                                         "本原型尚不支持对图片依据做完整独立评审：" + name))
            elif (attachment.get("excerpt_limit_reached") is True
                    or not isinstance(attachment.get("text"), str)
                    or len(attachment["text"]) >= MAX_EXCERPT_CHARS):
                findings.append(_finding("source_completeness_unknown", source.id,
                                         "附件缺少完整文本或摘要已达到裁剪边界，无法确认完整需求依据：" + name))
    return findings


def validate_review_sources(candidate):
    findings = review_source_findings(candidate)
    if findings:
        raise ValueError(findings[0].message)


# Backward-compatible import name, one implementation.
validate_attachment_excerpts = validate_review_sources


def _source_completeness(snapshot):
    # This input-only check deliberately precedes target/graph validation, so an
    # early unsupported-source receipt cannot be obscured by pending finalization.
    try:
        candidate = snapshot.candidate_project()
    except (ValidationError, ValueError, TypeError) as exc:
        return _result(snapshot, "source_completeness", ("candidate_source_input",),
                       (_finding("invalid_project_schema", "candidate", str(exc)[:2000]),))
    findings = review_source_findings(candidate)
    return _result(snapshot, "source_completeness", ("source_attachments",), findings,
                   verdict="unknown" if findings else "pass")


def _contract_source_history(snapshot):
    before, candidate = snapshot.before_project(), snapshot.candidate_project()
    findings = [_finding(f["code"], f["subject"], f["message"])
                for f in contract_findings(candidate, include_availability=False)
                + history_findings(before, candidate)]
    return _result(snapshot, "contract_source_history", ("source_anchors", "requirements",
                   "bindings", "process_activity", "source_requirement_process_history"), findings)


def _design_consistency(snapshot):
    candidate = snapshot.candidate_project()
    milestones = {m.id: m for m in candidate.milestones}
    behaviors = {b.id: b for b in candidate.behaviors}
    findings, missing, covered = [], [], ["current_design"]
    for finding in review_design(candidate)["findings"]:
        code, subject, message, severity = (finding[k] for k in ("code", "subject", "message", "severity"))
        if code == "unmapped_delivery":
            milestone = milestones[subject]
            owned = [behaviors.get(bid) for bid in milestone.behavior_revision_ids]
            tags = set(milestone.change_types)
            supporting = (bool(tags) and tags <= DECLARED_SUPPORTING_CHANGE_TYPES
                          and bool(owned) and all(b is not None and b.owner == subject
                                                  and b.acceptance_scope == "milestone" for b in owned))
            if supporting:
                code = "delivery_mapping_declared_not_applicable"
                message = ("已声明仅文档/测试类辅助交付且均为本步验收，组件映射不适用；"
                           "这是待语义复核的适用性声明，不证明没有产品影响或实现已通过")
                covered.append("delivery_mapping_not_applicable:" + subject)
            else:
                code, severity = "delivery_component_mapping_unknown", "error"
                missing.append(subject)
                keys = [b.behavior_key for b in owned if b is not None]
                message = ("交付缺少受影响组件引用。为这些契约的 component_ids 关联实际组件即可自动同步切片："
                           + ", ".join(keys) + "。允许复用现有组件，不需要新增专属组件；不要虚构映射")
        else:
            covered.append("design:" + code + ":" + subject)
        findings.append(_finding(code, subject, message, severity=severity))
    # A known invalid reference still blocks. Missing declarations are unknown,
    # so complete partial candidates can persist while publication stays held.
    invalid = any(f.severity == "error" and f.code != "delivery_component_mapping_unknown" for f in findings)
    verdict = "block" if invalid else "unknown" if missing else "pass"
    return _result(snapshot, "design_consistency", (*covered, *("delivery_mapping:" + mid for mid in missing)),
                   findings, verdict=verdict)


def _declared_availability(snapshot):
    candidate = snapshot.candidate_project()
    findings = [_finding(f["code"], f["subject"], f["message"])
                for f in declared_availability_findings(candidate)]
    ancestors = ancestor_sets({m.id: m.dependencies
                              for m in [*candidate.source_milestones, *candidate.milestones]})
    used = set().union(*(ancestors[m.id] for m in candidate.milestones)) if candidate.milestones else set()
    unresolved = []
    for source in candidate.source_milestones:
        if source.id in used and (not source.source_behaviors or candidate.baseline is None
                                  or not candidate.baseline.complete
                                  or source.source_baseline_id != candidate.baseline.id):
            unresolved.append(_finding("source_availability_unknown", source.id,
                                       "声明前置的源码能力缺少当前完整基线身份或可引用能力记录"))
    return _result(snapshot, "declared_availability",
                   ("slice_activation:" + m.id for m in candidate.milestones),
                   (*findings, *unresolved),
                   verdict="block" if findings else "unknown" if unresolved else "pass")


def _typed_capability_flow(snapshot):
    report = typed_capability_report(
        snapshot.candidate_project(), snapshot.before_project(),
        obligation_keys=snapshot.record_data().get("typed_obligation_keys", ()),
        consumption_obligation_keys=snapshot.record_data().get("consumption_obligation_keys", ()),
    )
    findings = [_finding(f["code"], f["subject"], f["message"], severity=f["severity"], refs=f["refs"])
                for f in report["findings"]]
    return _result(snapshot, "typed_capability_flow", report["covered_subjects"], findings,
                   verdict=report["verdict"], applicability_reason=report["applicability_reason"])


@dataclass(frozen=True)
class BuiltinPlugin:
    manifest: PluginManifest
    check: Callable | None = None


BUILTIN_REGISTRY = (
    BuiltinPlugin(PluginManifest(id="source_completeness", version="1", kind="deterministic",
                                scope="supported_complete_source_attachments"), _source_completeness),
    BuiltinPlugin(PluginManifest(id="graph_identity", version="2", kind="deterministic",
                                prerequisites=("source_completeness",),
                                scope="input_schema_graph_identity"), _graph_identity),
    BuiltinPlugin(PluginManifest(id="contract_source_history", version="2", kind="deterministic",
                                prerequisites=("graph_identity",),
                                scope="contracts_sources_process_history"), _contract_source_history),
    BuiltinPlugin(PluginManifest(id="design_consistency", version="2", kind="deterministic",
                                prerequisites=("graph_identity",),
                                scope="design_diagnostics_and_declared_component_mapping"), _design_consistency),
    BuiltinPlugin(PluginManifest(id="declared_availability", version="1", kind="deterministic",
                                prerequisites=("graph_identity",),
                                scope="declared_owner_prerequisite_capabilities"), _declared_availability),
    BuiltinPlugin(PluginManifest(id="typed_capability_flow", version="3", kind="deterministic",
                                prerequisites=("graph_identity", "contract_source_history"),
                                scope="declared_typed_acceptance_and_runtime_consumption"), _typed_capability_flow),
    BuiltinPlugin(PluginManifest(id="semantic_review", version="2", kind="model_opinion",
                                prerequisites=CURRENT_POLICY.deterministic_required,
                                scope="source_plan_semantic_opinion")),
)


def validate_registry(registry, policy=CURRENT_POLICY):
    by_id = {p.manifest.id: p for p in registry}
    if len(by_id) != len(registry):
        raise ValueError("Duplicate harness plugin ID")
    if set(policy.required) - by_id.keys():
        raise ValueError("Required policy plugin is missing")
    ordered, visiting = [], set()

    def visit(identity):
        if identity not in by_id:
            raise ValueError("Harness prerequisite is missing")
        if identity in visiting:
            raise ValueError("Harness prerequisite cycle")
        if identity in ordered:
            return
        visiting.add(identity)
        plugin = by_id[identity]
        if (plugin.manifest.kind == "deterministic") != (plugin.check is not None):
            raise ValueError("Harness plugin implementation/kind mismatch")
        for dependency in plugin.manifest.prerequisites:
            visit(dependency)
        visiting.remove(identity)
        ordered.append(identity)

    for plugin in registry:
        visit(plugin.manifest.id)
    return tuple(by_id[identity] for identity in ordered)


_ORDERED = validate_registry(BUILTIN_REGISTRY)
_BY_ID = {p.manifest.id: p for p in _ORDERED}
REGISTRY_HASH = content_hash([p.manifest.model_dump(mode="json") for p in _ORDERED])


class HarnessExecutionError(ValueError):
    def __init__(self, status, message, *, request_refs=()):
        if status not in {"unavailable", "timeout", "budget_exhausted", "invalid_output", "error"}:
            raise ValueError("Invalid harness failure status")
        self.status, self.request_refs = status, tuple(request_refs)
        super().__init__(message)


def _new_run(snapshot):
    return HarnessRun(run_id=uuid4().hex, snapshot_id=snapshot.snapshot_id,
                      candidate_id=snapshot.candidate_id, candidate_hash=snapshot.candidate_hash,
                      policy_version=CURRENT_POLICY.version, policy_hash=POLICY_HASH,
                      registry_hash=REGISTRY_HASH, status="running")


def _append(run, execution, **changes):
    rows = (*run.executions, execution)
    return HarnessRun.model_validate_json(canonical_json({
        **run.model_dump(mode="json"), "executions": [r.model_dump(mode="json") for r in rows],
        "decision": policy_decision(rows), **changes,
    }))


def _prerequisites_pass(plugin, run):
    rows = {row.plugin_id: row for row in run.executions}
    return all(execution_satisfied(rows.get(identity))
               for identity in plugin.manifest.prerequisites)


def _execution(plugin, snapshot, run, *, result=None, status="completed", elapsed=0.0,
               detail="", request_refs=(), external_certificate_json=None):
    manifest = plugin.manifest
    certificate_id = ""
    if result is not None:
        result = PluginResult.model_validate_json(result.model_dump_json())
        if (result.plugin_id != manifest.id or result.plugin_version != manifest.version
                or result.snapshot_id != snapshot.snapshot_id or result.scope != manifest.scope):
            raise ValueError("Harness result identity or declared scope differs")
        certificate_id = content_hash({
            "result": result.model_dump(mode="json"), "policy_hash": POLICY_HASH,
            "registry_hash": REGISTRY_HASH, "request_refs": list(request_refs),
            "external_certificate_hash": content_hash(json.loads(external_certificate_json))
                                         if external_certificate_json is not None else None,
            "prerequisites": [row.certificate_id for row in run.executions
                              if row.plugin_id in manifest.prerequisites],
        })
    return PluginExecution(plugin_id=manifest.id, plugin_version=manifest.version,
                           kind=manifest.kind, status=status, result=result,
                           elapsed_seconds=float(round(elapsed, 6)), detail=detail,
                           request_refs=tuple(request_refs), certificate_id=certificate_id)


def _failure(plugin, snapshot, run, exc, started):
    status = (exc.status if isinstance(exc, HarnessExecutionError) else "timeout"
              if isinstance(exc, TimeoutError) else "invalid_output"
              if isinstance(exc, (ValueError, TypeError, ProviderOutputError)) else "error")
    return _execution(plugin, snapshot, run, status=status, detail=str(exc)[:2000],
                      elapsed=time.monotonic() - started,
                      request_refs=getattr(exc, "request_refs", ()))


def _deterministic(snapshot, on_update=None):
    run = _new_run(snapshot)
    for plugin in _ORDERED:
        if plugin.manifest.kind != "deterministic":
            continue
        started = time.monotonic()
        if not _prerequisites_pass(plugin, run):
            row = _execution(plugin, snapshot, run, status="prerequisite_skipped",
                             detail="Required prerequisite did not complete with pass")
        else:
            try:
                row = _execution(plugin, snapshot, run, result=plugin.check(snapshot),
                                 elapsed=time.monotonic() - started)
            except Exception as exc:
                row = _failure(plugin, snapshot, run, exc, started)
        run = _append(run, row)
        if on_update:
            on_update(run)
    return run


def run_deterministic(snapshot):
    snapshot = _validated_snapshot(snapshot)
    return _deterministic(snapshot).model_copy(update={"status": "completed"})


def _semantic_result(snapshot, review, certificate):
    from .plan_review import (
        BATCH_VERSION,
        BatchSemanticReview,
        batch_review_packet,
        normalize_batch_review,
    )
    candidate = snapshot.candidate_project()
    raw_review = review.model_dump() if isinstance(review, SemanticReview) else review
    review = SemanticReview.model_validate(raw_review)
    validate_semantic_review(candidate, review)
    # Recheck exact raw bytes and normalized subject/evidence coverage, even if
    # the transport already checked them. A model-supplied verdict is not a gate.
    if certificate.get("schema_version") != BATCH_VERSION:
        raise ValueError("Required raw semantic certificate is missing or obsolete")
    batch = BatchSemanticReview.model_validate_json(certificate["raw_arguments"])
    packet = batch_review_packet(snapshot.before_project(), candidate, snapshot.record_data())
    normalized = normalize_batch_review(candidate, packet, batch)
    if batch.model_dump() != certificate.get("batch") or normalized != review:
        raise ValueError("Raw certificate and normalized semantic review differ")
    pointers = {subject: issue.evidence_refs for issue in batch.issues for subject in issue.subjects}
    findings = [_finding("semantic_contradiction" if check.verdict == "contradicted"
                         else "semantic_unknown", check.subject,
                         check.reason + "；反例/边界：" + check.counterexample,
                         basis=check.basis, refs=pointers.get(check.subject, ()))
                for check in review.checks if check.verdict != "supported"]
    findings.extend(_finding("semantic_observation_" + observation.kind, subject,
                             f"[{observation.id}] {observation.reason}", severity="review",
                             basis=observation.basis, refs=observation.evidence_refs)
                    for observation in batch.observations for subject in observation.subjects)
    verdict = ("block" if any(c.verdict == "contradicted" for c in review.checks) else "unknown"
               if any(c.verdict == "unknown" for c in review.checks) else "pass")
    return (_result(snapshot, "semantic_review", review_subjects(candidate), findings, verdict=verdict),
            canonical_json(review.model_dump()), canonical_json(certificate))


async def run_harness(snapshot, semantic_evaluator, on_update=None):
    """The caller owns shared provider budget and its sole invocation deadline.

    Its evaluator returns (review, certificate[, request_refs]); audit references
    are caller-authored local /metrics/calls/N pointers, never model output.
    """
    snapshot = _validated_snapshot(snapshot)
    run = _deterministic(snapshot, on_update)
    plugin = _BY_ID["semantic_review"]
    started = time.monotonic()
    review_json = certificate_json = None
    if not _prerequisites_pass(plugin, run):
        row = _execution(plugin, snapshot, run, status="prerequisite_skipped",
                         detail="Required deterministic check did not complete with pass")
    elif semantic_evaluator is None:
        row = _execution(plugin, snapshot, run, status="unavailable",
                         detail="No semantic evaluator supplied")
    else:
        try:
            evaluated = await semantic_evaluator(snapshot)
            if not isinstance(evaluated, (tuple, list)) or len(evaluated) not in {2, 3}:
                raise ValueError("Semantic evaluator must return review and certificate")
            review, certificate = evaluated[:2]
            request_refs = tuple(evaluated[2]) if len(evaluated) == 3 else ()
            if (len(request_refs) != len(set(request_refs)) or any(
                    not isinstance(ref, str) or not re.fullmatch(r"/metrics/calls/[0-9]+", ref)
                    for ref in request_refs)):
                raise ValueError("Semantic request references must identify local provider audit rows")
            result, review_json, certificate_json = _semantic_result(snapshot, review, certificate)
            row = _execution(plugin, snapshot, run, result=result,
                             elapsed=time.monotonic() - started, request_refs=request_refs,
                             external_certificate_json=certificate_json)
        except (asyncio.CancelledError, GeneratorExit) as exc:
            row = _execution(plugin, snapshot, run, status="cancelled",
                             elapsed=time.monotonic() - started, detail="Review cancelled")
            run = _append(run, row, status="cancelled")
            exc.harness_run = run
            if on_update:
                try:
                    on_update(run)
                except Exception:
                    # A concurrently discarded durable candidate cannot be
                    # overwritten. Preserve partial data on the original signal.
                    pass
            raise
        except Exception as exc:
            row = _failure(plugin, snapshot, run, exc, started)
    run = _append(run, row, status="completed", semantic_review_json=review_json,
                  model_certificate_json=certificate_json)
    if on_update:
        on_update(run)
    return run


def replay_harness(snapshot, stored_run, validate_model_certificate):
    """Rerun deterministic checks/current policy; replay exact model certificate.

    No asynchronous transport and no cross-run reuse. The validator is owned by
    the commit boundary and must validate its durable record without provider IO.
    """
    snapshot = _validated_snapshot(snapshot)
    data = stored_run.model_dump(mode="json") if isinstance(stored_run, HarnessRun) else stored_run
    saved = HarnessRun.model_validate_json(canonical_json(data))
    if (saved.snapshot_id != snapshot.snapshot_id or saved.candidate_id != snapshot.candidate_id
            or saved.candidate_hash != snapshot.candidate_hash or saved.policy_hash != POLICY_HASH
            or saved.policy_version != CURRENT_POLICY.version or saved.registry_hash != REGISTRY_HASH
            or saved.status != "completed"):
        raise ValueError("Harness input, registry or policy changed; revalidation required")
    if [r.plugin_id for r in saved.executions] != [p.manifest.id for p in _ORDERED]:
        raise ValueError("Harness execution set is incomplete or out of order")
    for row in saved.executions:
        plugin = _BY_ID[row.plugin_id]
        if row.plugin_version != plugin.manifest.version or row.kind != plugin.manifest.kind:
            raise ValueError("Harness implementation version changed; revalidation required")
        if row.result and row.result.snapshot_id != snapshot.snapshot_id:
            raise ValueError("Stored check belongs to another snapshot")
    run = _deterministic(snapshot)
    # Completed saved deterministic records must replay byte-for-byte, excluding
    # runner timing. Do not silently upgrade incomplete or obsolete executions.
    for old, current in zip(saved.executions, run.executions):
        if (old.status != current.status or old.result != current.result
                or old.certificate_id != current.certificate_id):
            raise ValueError("Stored deterministic certificate does not replay exactly")
    plugin = _BY_ID["semantic_review"]
    started = time.monotonic()
    review_json = certificate_json = None
    if not _prerequisites_pass(plugin, run):
        row = _execution(plugin, snapshot, run, status="prerequisite_skipped",
                         detail="Current deterministic policy holds publication")
    else:
        try:
            old = saved.executions[-1]
            if (old.status != "completed" or not saved.model_certificate_json
                    or not saved.semantic_review_json):
                raise ValueError("A complete exact model certificate is required")
            certificate = json.loads(saved.model_certificate_json)
            review = validate_model_certificate(snapshot, certificate)
            result, review_json, certificate_json = _semantic_result(snapshot, review, certificate)
            row = _execution(plugin, snapshot, run, result=result,
                             elapsed=time.monotonic() - started, request_refs=old.request_refs,
                             external_certificate_json=certificate_json)
            if (row.certificate_id != old.certificate_id or result != old.result
                    or review_json != saved.semantic_review_json
                    or certificate_json != saved.model_certificate_json):
                raise ValueError("Stored semantic certificate does not replay exactly")
        except Exception as exc:
            row = _failure(plugin, snapshot, run, exc, started)
            review_json = certificate_json = None
    return _append(run, row, status="completed", semantic_review_json=review_json,
                   model_certificate_json=certificate_json)
