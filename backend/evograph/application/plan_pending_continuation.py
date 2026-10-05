"""Explicit, pinned continuation of one stopped ordinary v2 generation stage.

Only ungenerated atoms of the same architecture record are postponed/recombined.
This is transport packing, never a user-facing milestone or semantic certificate.
"""
from copy import deepcopy
from types import SimpleNamespace

from ..agent_tools.base import ToolContext
from ..domain.models import Project
from ..domain.plan_contracts import IntentSource, candidate_hash, planning_payload
from .plan_batch_policy import PENDING_ARCHITECTURE_EXPERIMENT, experiment_policy
from .plan_ir import PlanDelta, compile_plan_delta
from .plan_patch import _completed_compiler_audit, _MemoryDatabase, propose_plan_patch
from .plan_units import (
    MAX_UNIT_BYTES,
    MAX_UNIT_CONTRACTS,
    MAX_UNIT_OPERATIONS,
    SchedulePlanChanges,
    _delta_rows,
    _hash,
    _identity,
    _manifest_rows,
    _operation_count,
    _partition_errors,
    _schedule,
    _schedule_view,
    _unit_changes,
    resume_unit_schedule,
    schedule_findings,
)

PIN_KEYS = {"project_id", "version", "predecessor_candidate_id", "predecessor_record_hash",
            "schedule_hash", "canonical_revision", "canonical_hash"}
ZERO_WRITE_RETRY = "zero-write-once/v1"
RETRY_PIN_KEYS = PIN_KEYS | {"retry"}


def recombine_pending_architecture(schedule):
    """Pure exact-row transformation; completed and non-architecture units stay exact."""
    result = deepcopy(schedule)
    completed = [u for u in result["units"] if u["state"] == "completed"]
    pending = [u for u in result["units"] if u["state"] != "completed"]
    architecture = [u for u in pending if all(
        r["kind"] == "architecture" and r["id"] == "architecture" for r in u["changes"])]
    retained = [u for u in pending if u not in architecture]
    if (len(completed) != 1 or result["units"][:1] != completed or len(pending) != 6
            or len(architecture) != 3 or len(retained) != 3
            or any(u["state"] != "pending" or u["holds"] for u in pending)
            or any(r["kind"] == "architecture" for u in retained for r in u["changes"])):
        raise ValueError("pending continuation requires one checkpoint, three metadata and three ordinary pending units")
    old_ids = {u["id"] for u in architecture}
    if any(old_ids & set(u["depends_on"]) for u in retained):
        raise ValueError("pending metadata cannot be postponed across a dependent ordinary unit")
    merged = deepcopy(architecture[-1])
    merged["changes"] = [deepcopy(row) for unit in architecture for row in unit["changes"]]
    if len({(u["existing_text_bytes"], u["estimated_text_bytes"]) for u in architecture}) != 1:
        raise ValueError("pending architecture sizing disagrees")
    merged.update(hash=_hash(merged["changes"]), contract_count=0,
                  depends_on=sorted({u["id"] for u in retained} | {
                      dep for u in architecture for dep in u["depends_on"] if dep not in old_ids}))
    result["units"] = completed + retained + [merged]
    result["pending_ids"] = [u["id"] for u in result["units"] if u["state"] != "completed"]
    for unit in result["units"]:
        operations = _operation_count(unit["changes"])
        if (operations > MAX_UNIT_OPERATIONS or unit["contract_count"] > MAX_UNIT_CONTRACTS
                or (unit["estimated_text_bytes"] > MAX_UNIT_BYTES and operations != 1)):
            raise ValueError("pending continuation exceeds unchanged packing bounds")
    if _partition_errors(result):
        raise ValueError("pending continuation did not preserve the admitted manifest")
    return result



def _replayed_planning_payload(before, project):
    """Normalize only fresh server-generated identities and timestamps, never plan fields."""
    payload = planning_payload(project)
    prior_ids = {b.id for b in before.behaviors}
    behaviors = payload["behaviors"]
    ids = [b["id"] for b in behaviors]
    if len(ids) != len(set(ids)) or any(b["id"] in prior_ids for b in behaviors[len(before.behaviors):]):
        raise ValueError("pending continuation behavior revision identity collision")
    new_ids = {b["id"]: f"generated-behavior-{i}" for i, b in enumerate(behaviors[len(before.behaviors):])}
    for behavior in behaviors:
        behavior["id"] = new_ids.get(behavior["id"], behavior["id"])
        behavior["supersedes"] = new_ids.get(behavior["supersedes"], behavior["supersedes"])
    for milestone in payload["milestones"]:
        milestone["behavior_revision_ids"] = [new_ids.get(k, k) for k in milestone["behavior_revision_ids"]]
    for target in payload["targets"]:
        target["required_behavior_ids"] = [new_ids.get(k, k) for k in target["required_behavior_ids"]]
    for binding in payload["plan_contract"]["bindings"]:
        key = binding["behavior_revision_id"]
        binding["behavior_revision_id"] = new_ids.get(key, key)
    for field in ("plans", "targets", "architectures"):
        for entry in payload[field][len(getattr(before, field)):]:
            entry["created_at"] = "generated-timestamp"
    return payload


def _validate_compiled_result(canonical, base, after, compiled, source_id):
    """Replay existing atomic services in memory; the supplied result is never authoritative."""
    memory = _MemoryDatabase(SimpleNamespace(
        canonical=SimpleNamespace(get=lambda _: canonical.model_copy(deep=True)),
        source_id=source_id, requirement_source_ids={source_id}), base)
    propose_plan_patch(ToolContext(base.id, SimpleNamespace(db=memory)), compiled.patch)
    if _replayed_planning_payload(base, memory.project) != _replayed_planning_payload(base, after):
        raise ValueError("pending continuation saved planning result differs from compiled checkpoint")


def _validate_pins(argument, before, previous, *, retry=False):
    keys = RETRY_PIN_KEYS if retry else PIN_KEYS
    if (set(argument) != keys or (retry and argument["retry"] != ZERO_WRITE_RETRY)
            or argument["version"] != PENDING_ARCHITECTURE_EXPERIMENT
            or argument["project_id"] != before.id
            or argument["canonical_revision"] != before.revision
            or argument["canonical_hash"] != candidate_hash(before)
            or argument["predecessor_candidate_id"] != previous["id"]
            or argument["predecessor_record_hash"] != _hash(previous)
            or argument["schedule_hash"] != _hash(previous["work_units"])):
        raise ValueError("pending continuation caller pin changed")


def admit_pending_continuation(argument, before, previous, content, *, store=None):
    """Validate caller pins, exact source/router evidence and the sole compiler checkpoint."""
    try:
        if "retry" in argument:
            return _admit_zero_write_retry(argument, before, previous, content, store)
        _validate_pins(argument, before, previous)
        candidate = Project.model_validate(previous["project"])
        schedule = previous["work_units"]
        if (previous["project_id"] != before.id or candidate.id != before.id
                or previous["base_revision"] != before.revision
                or previous["revision"] != candidate.revision or candidate.revision != before.revision + 1
                or previous["candidate_hash"] != candidate_hash(candidate)
                or previous["status"] != "needs_resolution" or previous.get("planning_experiment")
                or previous.get("resumes_candidate_id") or previous.get("prior_work_units")
                or previous.get("work_unit_schedule_history") or schedule.get("resumes")
                or previous.get("reviews") or previous.get("batch_reviews") or previous.get("harness_run")
                or previous.get("generation_progress", {}).get("checkpoint_count") != 1
                or previous.get("generation_pause_reason") != "call_budget_reserved_for_review"
                or schedule["version"] != "plan-units/v2" or len(schedule["checkpoints"]) != 1
                or schedule["origin_source_id"] != previous["id"] or schedule["source_id"] != previous["id"]
                or content != previous["input"] or content != schedule["input_text"]
                or schedule_findings(previous, candidate)):
            raise ValueError("pending continuation requires the unchanged stopped ordinary checkpoint")
        sources = candidate.plan_contract.sources
        if (len(sources) != len(before.plan_contract.sources) + 1
                or sources[:-1] != before.plan_contract.sources
                or sources[-1].id != previous["id"] or sources[-1].text != content
                or sources[-1].origin != "user" or sources[-1].evidence
                or sources[-1].reference_context != previous["reference_context"]):
            raise ValueError("pending continuation source coverage changed")
        base = before.model_copy(deep=True)
        base.plan_contract.sources.append(sources[-1].model_copy(deep=True))
        attempts = previous["tool_attempts"]
        routers = [a for a in attempts if a["name"] == "schedule_plan_changes"]
        successful = [a for a in attempts if a["name"] == "submit_plan_delta" and a["status"] == "succeeded"]
        if len(routers) != 1 or len(successful) != 1 or len(previous["compilations"]) != 1:
            raise ValueError("pending continuation router/compiler lineage changed")
        router, accepted = routers[0], successful[0]
        original = deepcopy(schedule)
        original["units"] = _schedule(base, _unit_changes(schedule["manifest"]))
        original.update(completed_ids=[], pending_ids=[u["id"] for u in original["units"]], checkpoints=[])
        expected_units = deepcopy(original["units"])
        expected_units[0]["state"] = "completed"
        if (router["status"] != "succeeded" or router["source_id"] != previous["id"]
                or _manifest_rows(SchedulePlanChanges.model_validate_json(router["raw_arguments"])) != schedule["manifest"]
                or router["payload"]["result"]["schedule"] != _schedule_view(original)
                or expected_units != schedule["units"] or _identity(base) != schedule["base_fingerprint"]):
            raise ValueError("pending continuation original admitted schedule changed")
        activity = [{"id": a["id"], "name": a["name"], "status": a["status"]} for a in attempts]
        if sources[-1].activity != activity or any(a["source_id"] != previous["id"] for a in attempts):
            raise ValueError("pending continuation source activity lineage changed")
        historical = deepcopy(activity[:attempts.index(accepted) + 1])
        historical[-1]["status"] = "started"
        base.plan_contract.sources[-1].activity = historical
        after = candidate.model_copy(deep=True)
        after.plan_contract.sources[-1].activity = deepcopy(historical)
        audit, checkpoint = previous["compilations"][0], schedule["checkpoints"][0]
        delta = PlanDelta.model_validate_json(accepted["raw_arguments"])
        raw, actual = _delta_rows(delta, fragmented=True)
        compiled = compile_plan_delta(base, raw)
        expected_checkpoint = {
            "unit_id": schedule["units"][0]["id"], "unit_hash": schedule["units"][0]["hash"],
            "source_id": previous["id"], "before_revision": before.revision, "after_revision": candidate.revision,
            "candidate_hash": candidate_hash(after), "delta_hash": _hash(raw),
            "change_ids": [r["change_id"] for r in schedule["units"][0]["changes"]],
            "submitted_fields": {k: sorted(r["fields"]) for k, r in actual.items()}, "changed": True,
        }
        if (raw != audit["ir"] or set(actual) != set(expected_checkpoint["change_ids"])
                or checkpoint != expected_checkpoint
                or audit != _completed_compiler_audit(compiled.audit, base, after, changed=True)):
            raise ValueError("pending continuation completed compiler/checkpoint lineage changed")
        _validate_compiled_result(before, base, after, compiled, previous["id"])
        transformed = recombine_pending_architecture(schedule)
        if schedule_findings({**previous, "work_units": transformed}, candidate):
            raise ValueError("pending continuation dependency preflight failed")
        mapping = {u["id"]: next(v["id"] for v in transformed["units"]
                   if {r["change_id"] for r in u["changes"]} <= {r["change_id"] for r in v["changes"]})
                   for u in schedule["units"] if u["state"] != "completed"}
        return transformed, {"pins": deepcopy(argument), "compiler_lineage_hash": _hash(previous["compilations"]),
                             "transformed_schedule_hash": _hash(transformed), "pending_unit_mapping": mapping}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("malformed pending continuation evidence") from exc


def _admit_zero_write_retry(argument, before, previous, content, store):
    """One additional attempt, rooted in the original ordinary compiler proof."""
    _validate_pins(argument, before, previous, retry=True)
    if _hash(store.latest(before.id)) != _hash(previous):
        raise ValueError("pending retry predecessor is no longer latest")
    original = store.get(previous["resumes_candidate_id"])
    # Calling the ordinary admission, not this retry path, prevents recursion.
    old_admission = previous["pending_architecture_admission"]
    if set(old_admission["pins"]) != PIN_KEYS:
        raise ValueError("pending retry cannot retry a retry successor")
    transformed, verified = admit_pending_continuation(
        old_admission["pins"], before, original, content)
    policy = {"project_id": before.id, **experiment_policy(PENDING_ARCHITECTURE_EXPERIMENT)}
    candidate = Project.model_validate(previous["project"])
    parent = Project.model_validate(original["project"])
    attempts = previous["tool_attempts"]
    if (previous["id"] == original["id"] or previous["turn_id"] != previous["id"]
            or previous["project_id"] != before.id or previous["base_revision"] != before.revision
            or previous["revision"] != parent.revision or candidate.revision != parent.revision
            or previous["candidate_hash"] != candidate_hash(candidate)
            or previous["status"] != "needs_resolution" or previous["input"] != content
            or previous["reference_context"] != original["reference_context"]
            or previous["planning_experiment"] != policy or old_admission != verified
            or previous["prior_work_units"] != original["work_units"]
            or previous.get("work_unit_schedule_history")
            or previous["compilations"] != original["compilations"]
            or previous.get("reviews") or previous.get("batch_reviews") or previous.get("harness_run")
            or previous.get("validation_requested") or previous.get("tool_calls")
            or previous["generation_progress"] != {"state": "staged", "checkpoint_count": 1}
            or previous["generation_pause_reason"] != "experiment_first_failure"
            or previous["metrics"].get("experiment_stopped") != "experiment_first_failure"
            or previous["metrics"]["provider_calls"] != 1
            or previous["metrics"]["planning_experiment"] != policy or len(attempts) != 1):
        raise ValueError("pending retry requires the unchanged zero-write first failure")
    attempt = attempts[0]
    if (attempt["id"] != 1 or attempt["name"] != "submit_plan_delta"
            or attempt["status"] != "failed" or attempt["source_id"] != previous["id"]
            or attempt["generation_call"] != 1 or attempt["payload"].get("ok") is not False
            or not attempt["payload"].get("error")):
        raise ValueError("pending retry failed activity changed")
    source = IntentSource(id=previous["id"], text=content,
                          reference_context=original["reference_context"])
    expected = parent.model_copy(deep=True)
    expected.plan_contract.sources.append(source)
    resumed = resume_unit_schedule({**original, "work_units": transformed}, expected, previous["id"], content)
    if resumed != previous["work_units"]:
        raise ValueError("pending retry schedule or completed checkpoint changed")
    source.message_id = previous["source_message_id"]
    unit = next(u for u in resumed["units"] if u["state"] == "pending")
    pin = {"unit_id": unit["id"], "unit_hash": unit["hash"], "source_id": source.id,
           "manifest_hash": resumed["manifest_hash"], "revision": parent.revision,
           "planning_fingerprint": _identity(expected), "candidate_hash": candidate_hash(expected),
           "accepted_delta_hash": None}
    source.activity = [{"id": attempt["id"], "name": attempt["name"], "status": attempt["status"]}]
    if (not source.message_id or expected != candidate or previous["unit_request"] != pin
            or schedule_findings(previous, candidate)):
        raise ValueError("pending retry saved result differs from its zero-write source/activity")
    return deepcopy(resumed), {
        **verified, "pins": deepcopy(argument), "transformed_schedule_hash": _hash(resumed),
        "zero_write_retry": {"original_candidate_id": original["id"],
                             "original_record_hash": _hash(original),
                             "original_admission_hash": _hash(verified)},
    }


def pending_continuation_errors(record):
    """Recheck immutable transformation/old checkpoint before dispatch and atomic write."""
    try:
        admission = record["pending_architecture_admission"]
        prior = record["prior_work_units"]
        pins = admission["pins"]
        retry = admission.get("zero_write_retry")
        if retry is not None:
            if (set(pins) != RETRY_PIN_KEYS or pins["retry"] != ZERO_WRITE_RETRY
                    or set(retry) != {"original_candidate_id", "original_record_hash", "original_admission_hash"}
                    or retry["original_candidate_id"] != prior["origin_source_id"]
                    or len(prior["resumes"]) != 1 or len(prior["checkpoints"]) != 1):
                return ["pending retry lineage changed"]
            expected = prior  # Already transformed, verified once; never repack a retry.
        else:
            if set(pins) != PIN_KEYS:
                return ["pending continuation caller pin changed"]
            expected = recombine_pending_architecture(prior)
        current = record["work_units"]
        if (pins["version"] != PENDING_ARCHITECTURE_EXPERIMENT
                or pins["project_id"] != record["project_id"]
                or pins["predecessor_candidate_id"] != record["resumes_candidate_id"]
                or pins["canonical_revision"] != record["base_revision"]
                or _hash(prior) != pins["schedule_hash"]
                or _hash(expected) != admission["transformed_schedule_hash"]
                or _hash(record["compilations"][:1]) != admission["compiler_lineage_hash"]
                or current["checkpoints"][:1] != prior["checkpoints"]
                or current["units"][:1] != prior["units"][:1]
                or len(current["units"]) != len(expected["units"])
                or any({k: v for k, v in a.items() if k != "state"}
                       != {k: v for k, v in b.items() if k != "state"}
                       for a, b in zip(current["units"], expected["units"]))):
            return ["pending continuation transformation or checkpoint lineage changed"]
        return []
    except (KeyError, TypeError, ValueError, AttributeError):
        return ["missing or malformed pending continuation admission"]
