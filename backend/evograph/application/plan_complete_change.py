"""Versioned nonempty evolution: one complete delta, one full review, no retry.

This is an admission mode for the existing compiler and harness, not a second
planner or schedule. Counts are submitted delta identities, including unchanged
restatements; they are never the total number of objects in the saved project.
"""
from copy import deepcopy
from dataclasses import replace

from ..domain.models import Project
from ..domain.plan_contracts import active_behaviors
from .plan_budget import encoded_size

COMPLETE_CHANGE_VERSION = "bounded-complete-change/v1"
COMPLETE_CHANGE_LIMITS = {"max_phases": 1, "max_calls": 2, "max_input_bytes": 393216}
COMPLETE_CHANGE_POLICY = {
    "version": COMPLETE_CHANGE_VERSION,
    "result_verifier": "whole-patch-replay/v1",
    "bounds": {"operations": 32, "slices": 6, "contracts": 8, "field_atoms": 64,
               "fields": 256, "references": 256, "delta_bytes": 65536},
    "counting": "submitted_delta_rows_including_unchanged_restatements",
    "request_sequence": ["generation", "semantic_review"],
}
COMPLETE_CHANGE_PROMPT = """You are the complete-change generator for a nonempty saved plan.
Return exactly ONE submit_plan_delta tool call containing the complete coherent change for
this user request, or ONE ask_user for an unavoidable user-owned decision. No router,
per-unit output, second generation, repair loop, partial continuation or extra tool call is
available. The complete response is compiled together, then independently reviewed once.
One atomic planning change may contain MULTIPLE separate coherent milestones, each feasible
for one coding-agent session. Do not collapse a large plan into one giant coding deliverable.
Only submit changed identities/fields. Every submitted identity (even an unchanged restatement)
spends the supplied bounds. Omitted fields remain exact; explicit lists replace their contents.
The existing PlanDelta fields are the only language: owner, requires_behavior_keys, dependencies,
and their reasons can change together. All resulting references must be coherent in the whole
candidate. Earlier acceptance must be achievable at that stage through real prerequisites;
a later control, future test, warning or unsupported external guarantee cannot satisfy it.
Preserve all original user sources, accepted/draft history and every retained entry claim.
The retained baseline IDs and dispositions are server-owned. Distinguish accepted canonical
claims, unapproved draft corrections, and explicitly authorized withdrawal. A matching quote
is evidence of wording, never semantic permission to weaken acceptance. Old pending work is
an exact historical proposal, not a fresh user requirement and not completed merely by silence.
Address relevant current and inherited findings; assess their applicability rather than blindly
copying old proposals. Preserve bounded recovery, ambiguous external outcomes and realistic
availability. Unknown external idempotency/lookup guarantees remain explicit unresolved facts.
Healthy probes cannot reset permanent failures forever. Do not claim no missed or duplicated
catch-up work without a feasible mechanism. Untyped capabilities remain honestly unmodeled;
do not manufacture typed coverage or provider guarantees to satisfy a check.
Use exact allowed source quotes for new requirements. No source text is truncated. Resolve
lossless same_text_as_source_id and retained aliases before interpreting or quoting them.
Do not include credentials, execute code, or claim the proposed changes were implemented.
"""


def is_complete_change(record):
    return (record.get("planning_experiment") or {}).get("version") == COMPLETE_CHANGE_VERSION


def admission(project, record):
    from .plan_phase import intent_source_id
    from .plan_units import _identity
    if not active_behaviors(project):
        raise ValueError("complete-change admission requires a nonempty active planning base")
    if record.get("work_units") or record.get("unit_request") or record.get("compilations"):
        raise ValueError("complete-change admission must start a new request, not resume an old attempt")
    return {"policy": deepcopy(COMPLETE_CHANGE_POLICY), "project_id": project.id,
            "source_id": intent_source_id(record), "base_revision": project.revision,
            "base_fingerprint": _identity(project),
            "starting_checkpoints": record.get("generation_progress", {}).get("checkpoint_count", 0)}


def measure_delta(args):
    from .plan_ir import PlanDelta
    from .plan_units import _delta_rows
    delta = PlanDelta.model_validate(args.model_dump(exclude_unset=True) if isinstance(args, PlanDelta) else args)
    raw, rows = _delta_rows(delta)
    _, atoms = _delta_rows(delta, fragmented=True)
    references = sum(len(row["uses"]) for row in rows.values())
    references += sum(len(capability.get("consumes") or [])
                      for contract in raw.get("contracts", [])
                      for capability in contract.get("provides") or [])
    measured = {"operations": len(rows),
                "slices": sum(k.startswith(("slice:", "remove_slice:")) for k in rows),
                "contracts": sum(k.startswith(("contract:", "remove_contract:")) for k in rows),
                "field_atoms": len(atoms), "fields": sum(len(r["fields"]) for r in rows.values()),
                "references": references, "delta_bytes": encoded_size(raw)}
    if not rows:
        raise ValueError("complete-change requires a substantive nonempty delta")
    for key, value in measured.items():
        if value > COMPLETE_CHANGE_POLICY["bounds"][key]:
            raise ValueError(f"complete-change submitted {key} {value} exceeds policy {COMPLETE_CHANGE_POLICY['bounds'][key]}")
    return raw, measured


def validate_admission(record, project):
    from .plan_phase import intent_source_id
    from .plan_units import _identity
    pin = record.get("complete_change_admission")
    if (not is_complete_change(record) or not isinstance(pin, dict)
            or pin.get("policy") != COMPLETE_CHANGE_POLICY
            or pin.get("project_id") != project.id or pin.get("source_id") != intent_source_id(record)
            or record.get("work_units") or record.get("unit_request")):
        raise ValueError("complete-change policy, mode, source or admission changed")
    audits = record.get("compilations", [])
    if not audits:
        if (project.revision != pin["base_revision"] + int(project.question is not None)
                or _identity(project) != pin["base_fingerprint"]):
            raise ValueError("complete-change admitted candidate is stale")
    elif len(audits) != 1:
        raise ValueError("complete-change permits exactly one compiler checkpoint")
    return pin


def validate_delta(db, args):
    from .plan_units import _hash, _identity
    raw, _ = measure_delta(args)
    pin = validate_admission(db.record, db.project)
    audits = db.record.get("compilations", [])
    if audits:
        if (not complete(db.record) or _hash(raw) != _hash(audits[0]["ir"])
                or _identity(db.project) != db.record["generation_progress"]["planning_fingerprint"]):
            raise ValueError("complete-change already completed; changed replay is forbidden")
        return True
    if db.project.revision != pin["base_revision"]:
        raise ValueError("complete-change base revision changed")
    return False


def checkpoint(record, before, after, audit):
    from .plan_units import _identity
    if audit is None and question_only_change(before, after):
        validate_admission(record, before)
        return record
    previous = {**record, "compilations": record.get("compilations", [])[:-1]}
    pin = validate_admission(previous, before)
    if (audit is None or len(record.get("compilations", [])) != 1
            or _identity(before) == _identity(after)
            or after.revision != before.revision + 1):
        raise ValueError("complete-change needs one substantive atomic checkpoint, not metadata-only progress")
    measure_delta(audit["ir"])
    record["generation_progress"] = {**record["generation_progress"],
        "checkpoint_count": pin["starting_checkpoints"] + 1,
        "planning_fingerprint": _identity(after)}
    return record


def complete(record):
    from .plan_units import _hash, _identity
    try:
        project = Project.model_validate(record["project"])
        pin = validate_admission(record, project)
        audits = record.get("compilations", [])
        if len(audits) != 1:
            return False
        audit = audits[0]
        measure_delta(audit["ir"])
        return (audit["audit_hash"] == _hash({k: v for k, v in audit.items() if k != "audit_hash"})
                and audit["base_revision"] == pin["base_revision"]
                and audit["result_revision"] == project.revision == pin["base_revision"] + 1
                and record["generation_progress"]["checkpoint_count"] == pin["starting_checkpoints"] + 1
                and record["generation_progress"]["planning_fingerprint"] == _identity(project))
    except (ValueError, KeyError, TypeError):
        return False


def question_only_change(before, after):
    ignored = {"question", "revision", "updated_at"}
    return (before.question is None and after.question is not None
            and after.revision == before.revision + 1
            and before.model_dump(exclude=ignored) == after.model_dump(exclude=ignored))


def guard_write(data, previous, job, *, canonical, dispatch=False, commit=False):
    """Runs under the existing job writer lock; no separate durable state ledger."""
    from ..infrastructure.database import ConflictError
    from .plan_ir import compile_plan_delta
    from .plan_patch import _completed_compiler_audit, verify_compiled_result
    from .plan_units import _identity
    project = Project.model_validate(data["project"])
    try:
        validate_admission(data, project)
        if (job.get("mode") != COMPLETE_CHANGE_VERSION or job["limits"] != COMPLETE_CHANGE_LIMITS
                or data["complete_change_admission"]["starting_checkpoints"] != job["retained_checkpoints"]):
            raise ValueError("complete-change job mode or limits changed")
        if previous is None:
            if data.get("complete_change_admission") != admission(project, data):
                raise ValueError("complete-change initial admission mismatch")
            return
        if data.get("complete_change_admission") != previous.get("complete_change_admission"):
            raise ValueError("complete-change admission cannot change")
        old = Project.model_validate(previous["project"])
        audits, prior = data.get("compilations", []), previous.get("compilations", [])
        structural = _identity(old) != _identity(project) or old.revision != project.revision
        if question_only_change(old, project) and not audits and not prior:
            if job["cancelled"] or job["status"] != "running":
                raise ValueError("complete-change question is stopped or cancelled")
            return
        if structural or len(audits) != len(prior):
            if job["cancelled"] or job["status"] != "running" or len(prior) or len(audits) != 1:
                raise ValueError("complete-change checkpoint is stopped, cancelled or already complete")
            validate_admission(previous, old)
            compiled = compile_plan_delta(old, audits[0]["ir"], retained_record=previous)
            expected = _completed_compiler_audit(compiled.audit, old, project, changed=True)
            if audits[0] != expected or not complete(data):
                raise ValueError("complete-change compiler audit or whole checkpoint changed")
            verify_compiled_result(canonical, old, project, previous, compiled)
        elif audits != prior:
            raise ValueError("complete-change compiler audit is immutable")
        if commit and not complete(data):
            raise ValueError("complete-change cannot apply an incomplete delta")
    except (ValueError, KeyError, TypeError) as exc:
        raise ConflictError(str(exc)) from exc


def complete_change_context(db):
    from .plan_continuation_context import prior_pending_intent_context
    from .plan_units import _initial_unit_context, _objects
    from .retained_acceptance import generation_context, project_router_retained_context
    project = db.project
    objects = _objects(project)
    context = _initial_unit_context(db, project, objects)
    context["retained_acceptance"] = generation_context(db.record, project)
    context["completed_contract_mechanisms"] = [
        {"key": value["key"], "revision_id": value["revision_id"], "mechanism": value["mechanism"]}
        for key, value in objects.items() if key.startswith("contract:")]
    context["completed_contract_mechanisms_note"] = "Exact mechanisms of EVERY active contract; proposals, not proof."
    for row in context["slices"]:
        milestone = project.milestone(row["id"])
        row.update(milestone.model_dump(include={"dependency_reasons", "attachment_ids", "resources", "change_types"}))
    context["slices_note"] = "Exact current delivery scope, prerequisites/reasons and metadata, joined to contract owners."
    context["components"] = [
        {k: v for k, v in value.items() if k in {"id", "label", "description", "role", "source_refs"}}
        for key, value in objects.items() if key.startswith("component:")]
    context["components_note"] = "Every current component record; no truncation."
    context["source_slices"] = [m.model_dump(include={"id", "title", "intent", "scope",
        "dependencies", "dependency_reasons", "source_behaviors", "source_baseline_id"})
        for m in project.source_milestones]
    context["prior_work_units"] = prior_pending_intent_context(db.record, project.id)
    for key in ("allowed_requirement_source_ids", "typed_obligation_keys", "consumption_obligation_keys"):
        context[key] = deepcopy(db.record.get(key, []))
    context["complete_change_admission"] = deepcopy(db.record["complete_change_admission"])
    context["omitted"] = ("Older project/revision history (except retained originals), raw old arguments and old full review packets are not "
        "retransmitted. Every active statement/mechanism, architecture, source request, retained original claim "
        "and exact pending proposal is supplied. All history remains protected by compiler checks.")
    for key in ("manifest_field_catalog", "manifest_field_note", "schedule"):
        context.pop(key, None)
    return project_router_retained_context(context, db.record.get("retained_acceptance"))


def delta_tool():
    from .plan_ir import DELTA_TOOL
    details = DELTA_TOOL.description.split("omissions retain data, null is invalid. ", 1)[1]
    details = details.replace("in the same atomic segment or earlier; manifest provides uses still declare definitions only.",
                              "in the complete candidate at the actual acceptance stage.")
    details = details.replace("assigned original contract", "original contract")
    details = details.replace("never a later unit", "never a later milestone")
    return replace(DELTA_TOOL, description=(
        "Submit the ONE complete bounded change for this entire request atomically. "
        "Multiple separate coding-session milestones are allowed. No later segment or repair is available. "
        "Omissions preserve existing fields; explicit lists replace them; null is invalid. "
        + details))
