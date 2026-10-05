"""Read-only feedback for one repair of a sealed harness run.

This is a prompt projection, never a publication decision or reusable certificate.
Full reports, historical findings and certificates remain in the candidate audit.
"""
import json
from copy import deepcopy

from ..domain.models import Project
from ..domain.plan_contracts import SemanticReview
from ..domain.plan_harness import (
    CURRENT_POLICY,
    HarnessRun,
    HarnessSnapshot,
    content_hash,
    execution_satisfied,
)
from .plan_harness import replay_harness, seal_snapshot
from .plan_review import (
    BATCH_VERSION,
    BatchSemanticReview,
    batch_review_packet,
    normalize_batch_review,
    validate_batch_certificate,
)

REPAIR_INSTRUCTIONS = """
Repair this candidate once, preserving exact user intent. Use repair_agenda in the current state
as the only current review-task list. Schedule changes needed to resolve those blocking issues,
including necessary cross-owner effects; do not add optional metadata work merely to fill empty fields.
The agenda describes the sealed pre-repair snapshot. Reconcile it with actual saved changes as units
complete; do not replay resolved work. Evidence pointers name that exact review packet, not later state.
All sources and saved planning facts remain constraints. Fresh full checks are still required after repair.
"""


def explicit_repair_agenda(before, record):
    """Replay the exact persisted held review before admitting explicit repair.

    This happens before appending the new source. The resulting agenda keeps the
    original run identity and is historical feedback, never a new certificate.
    """
    run = HarnessRun.model_validate_json(json.dumps(record.get("harness_run")))
    matches = [item for item in record.get("harness_snapshots", [])
               if item.get("snapshot_id") == run.snapshot_id]
    if len(matches) != 1:
        raise ValueError("Explicit repair requires one matching sealed snapshot")
    snapshot = HarnessSnapshot.model_validate_json(json.dumps(matches[0]))
    if (record.get("status") != "needs_resolution" or run.decision != "hold"
            or run.candidate_hash != record.get("candidate_hash")
            or snapshot != seal_snapshot(before, Project.model_validate(record["project"]), record)
            or any(row.status != "completed" for row in run.executions
                   if row.plugin_id in CURRENT_POLICY.required)):
        raise ValueError("Explicit repair requires the exact completed held candidate review")

    def replay_model(sealed, certificate):
        if not record.get("batch_reviews") or certificate != record["batch_reviews"][-1]:
            raise ValueError("Explicit repair certificate differs from its persisted audit")
        return validate_batch_certificate(sealed.before_project(), sealed.candidate_project(), record)

    replayed = replay_harness(snapshot, run, replay_model)
    if (replayed.decision != "hold" or replayed.semantic_review_json != run.semantic_review_json
            or replayed.model_certificate_json != run.model_certificate_json):
        raise ValueError("Explicit repair requires a verified held review certificate")
    agenda = build_repair_agenda(run, snapshot)
    if not agenda["plugins"]:
        raise ValueError("Explicit repair requires a verified blocking agenda")
    return agenda


def build_repair_agenda(run, snapshot, *, policy=CURRENT_POLICY):
    """Project required, completed, policy-unsatisfied checks, without truncation.

    The runner has already validated the executions. Recheck sealed identity and
    semantic normalization before using batch issue grouping to avoid duplicate
    per-subject findings. Transport failures are handled by the caller's existing
    controlled-retry path, not by this model-repair projection.
    """
    if (run.status != "completed" or run.snapshot_id != snapshot.snapshot_id
            or run.candidate_id != snapshot.candidate_id
            or run.candidate_hash != snapshot.candidate_hash
            or run.policy_version != policy.version
            or run.policy_hash != content_hash(policy.model_dump(mode="json"))):
        raise ValueError("Repair feedback requires the current policy and matching sealed run")
    if any(row.status in {"unavailable", "timeout", "budget_exhausted", "invalid_output",
                          "error", "cancelled"} for row in run.executions
           if row.plugin_id in policy.required):
        raise ValueError("Incomplete checks require controlled retry, not model repair")
    plugins = []
    for row in run.executions:
        if row.result and row.result.snapshot_id != snapshot.snapshot_id:
            raise ValueError("Repair feedback result belongs to another snapshot")
        if (row.plugin_id not in policy.required or row.status != "completed"
                or execution_satisfied(row, policy=policy)):
            continue
        result = row.result
        plugin = {"plugin_id": row.plugin_id, "plugin_version": row.plugin_version,
                  "verdict": result.verdict, "scope": result.scope,
                  "certificate_id": row.certificate_id, "request_refs": list(row.request_refs),
                  "evidence_refs": list(result.evidence_refs)}
        if row.plugin_id == "semantic_review":
            certificate = json.loads(run.model_certificate_json)
            if certificate.get("schema_version") != BATCH_VERSION:
                raise ValueError("Repair feedback requires the current semantic certificate")
            batch = BatchSemanticReview.model_validate_json(certificate["raw_arguments"])
            if batch.model_dump() != certificate["batch"]:
                raise ValueError("Repair feedback batch differs from its raw certificate")
            packet = batch_review_packet(snapshot.before_project(), snapshot.candidate_project(),
                                         snapshot.record_data())
            normalized = normalize_batch_review(snapshot.candidate_project(), packet, batch)
            if normalized != SemanticReview.model_validate_json(run.semantic_review_json):
                raise ValueError("Repair feedback batch differs from its normalized review")
            if {subject for issue in batch.issues for subject in issue.subjects} != {
                    finding.subject for finding in result.findings if finding.severity == "error"}:
                raise ValueError("Repair feedback does not cover the execution findings")
            plugin["review_scope_hash"] = batch.review_scope_hash
            # One exact issue preserves all its subjects; normalized findings and
            # supported placeholders must not become additional repair tasks.
            plugin["issues"] = [issue.model_dump(mode="json") for issue in batch.issues]
        else:
            blocking = [f for f in result.findings if f.severity == "error"]
            findings = {content_hash(f.model_dump(mode="json")): f.model_dump(mode="json")
                        for f in (blocking or result.findings)}
            plugin["issues"] = [{"id": row.plugin_id + ":" + identity, **finding}
                                for identity, finding in findings.items()]
            if not findings:
                # Unknown/N/A-without-policy-exception can hold without an error
                # finding. Preserve that fact explicitly rather than invent a fix.
                plugin["covered_subjects"] = list(result.covered_subjects)
                plugin["applicability_reason"] = result.applicability_reason
                plugin["detail"] = row.detail
        plugins.append(plugin)
    return {"schema_version": "plan-repair-agenda/v1",
            "run_id": run.run_id, "snapshot_id": run.snapshot_id,
            "candidate_id": run.candidate_id, "candidate_hash": run.candidate_hash,
            "candidate_revision": snapshot.candidate_revision,
            "policy_version": run.policy_version, "policy_hash": run.policy_hash,
            "registry_hash": run.registry_hash, "plugins": plugins,
            "scope_note": "Only required completed checks unsatisfied under the sealed run policy. "
                          "No supported checks, satisfied-plugin advisories or inherited issues are repair tasks. "
                          "Evidence refs address the original sealed review packet. This is not a pass certificate; "
                          "later saved changes require fresh review."}


def auto_repair_context(context, agenda):
    """Opt in only for repair; preserve ordinary generation byte-for-byte."""
    if agenda is None:
        return context
    excluded = {"findings", "inherited_findings", "inherited_semantic_findings", "findings_note",
                "prior_work_units"}
    feedback = deepcopy(agenda)
    feedback["applicability"] = ("current_reviewed_snapshot"
        if context.get("candidate_hash") == agenda["candidate_hash"]
        else "historical_needs_recheck")
    return {**{key: value for key, value in context.items() if key not in excluded},
            "repair_agenda": feedback}
