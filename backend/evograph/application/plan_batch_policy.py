"""Explicit bounded experiment policy. No provider/settings/UI defaults change."""
import json
from copy import deepcopy

from ..domain.models import Project
from ..domain.plan_contracts import planning_payload
from .plan_budget import encoded_size

COLD_START_EXPERIMENT = "bounded-empty-plan/v1"
REPAIR_EXPERIMENT = "bounded-single-unit-repair/v1"
BATCH_POLICY = {
    "version": COLD_START_EXPERIMENT,
    "bounds": {"operations": 32, "slices": 6, "contracts": 8, "field_atoms": 64,
               "fields": 256, "references": 256, "manifest_bytes": 32768,
               "unit_bytes": 49152, "context_bytes": 65536, "delta_bytes": 65536},
}
EMPTY_RESIDUE_FIELDS = {
    "baselines", "evidence", "source_diagram", "source_summary", "source_fingerprint",
    "source_file_fingerprints", "light_checks", "acceptance_requests", "uml_diagrams",
}
EXPERIMENT_LIMITS = {"max_calls": 3, "max_request_bytes": 163840,
                     "max_total_input_bytes": 393216, "max_output_bytes": 98304,
                     "call_timeout_seconds": 180}


def experiment_policy(name):
    if name not in {COLD_START_EXPERIMENT, REPAIR_EXPERIMENT}:
        raise ValueError("unsupported bounded planning experiment")
    return {"version": name, "limits": deepcopy(EXPERIMENT_LIMITS),
            "request_sequence": ["router", "generation", "semantic_review"]}


def empty_planning_base(project):
    """Source records are evidence, not prior planning; every other field is empty."""
    payload = planning_payload(project)
    payload["plan_contract"]["sources"] = []
    empty = Project(name="empty")
    return (payload == planning_payload(empty)
            and project.model_dump(include=EMPTY_RESIDUE_FIELDS)
            == empty.model_dump(include=EMPTY_RESIDUE_FIELDS))


def _checkpoint_evidence(record):
    from .plan_units import has_completed_units
    return bool(record.get("compilations") or record.get("generation_progress", {}).get("checkpoint_count")
                or has_completed_units(record.get("prior_work_units"))
                or any(has_completed_units(s) for s in record.get("work_unit_schedule_history", [])))


def new_batch_admission(project, record, rows):
    if (not empty_planning_base(project) or record.get("work_units") or record.get("prior_work_units")
            or record.get("resumes_candidate_id") or record.get("work_unit_schedule_history")
            or _checkpoint_evidence(record)):
        raise ValueError("bounded empty-plan policy requires a new empty planning base and no saved schedule/checkpoints")
    validate_batch_manifest(project, rows)
    return {"policy": deepcopy(BATCH_POLICY), "base": planning_payload(project),
            "base_revision": project.revision, "residue": project.model_dump(mode="json", include=EMPTY_RESIDUE_FIELDS)}


def validate_batch_manifest(project, rows):
    from .plan_units import SIZE_HOLD, SchedulePlanChanges, _manifest_rows, _schedule, _unit_changes
    canonical = _manifest_rows(SchedulePlanChanges.model_validate({"changes": [
        {k: r[k] for k in ("kind", "id", "fields", "uses", "intent")} for r in rows]}), project)
    if canonical != rows:
        raise ValueError("bounded batch manifest is not canonical")
    kinds = {row["kind"] for row in rows}
    if (kinds - {"target", "requirement", "process_constraint", "slice", "contract", "component", "relation", "architecture"}
            or not {"requirement", "slice", "contract", "component", "architecture"} <= kinds):
        raise ValueError("bounded batch requires a complete new plan manifest, without removals or retirements")
    definitions = {(r["kind"], r["id"]) for r in rows}
    capabilities = [(u["kind"], u["id"]) for r in rows for u in r["uses"] if u["field"] == "provides"]
    if len(capabilities) != len(set(capabilities)):
        raise ValueError("bounded batch duplicate capability definition")
    definitions.update(capabilities)
    for row in rows:
        uses = row["uses"]
        if (len(uses) != len({(u["field"], u["kind"], u["id"]) for u in uses})
                or any(u["field"] not in row["fields"] or (u["kind"], u["id"]) not in definitions for u in uses)):
            raise ValueError("bounded batch manifest references must be declared, unique and closed")
        if row["kind"] == "contract":
            if (sum(u["field"] == "owner" for u in uses) != 1
                    or not any(u["field"] == "requirement_ids" for u in uses)):
                raise ValueError("bounded batch contract needs declared owner and requirement coverage")
        if row["kind"] == "relation":
            source, target, _ = json.loads(row["id"])
            if {(u["field"], u["id"]) for u in uses} != {("source", source), ("target", target)}:
                raise ValueError("bounded batch relation endpoints must match declared uses")
    atoms = _unit_changes(rows)
    bounds = BATCH_POLICY["bounds"]
    measured = {"operations": len(rows), "slices": sum(r["kind"] == "slice" for r in rows),
                "contracts": sum(r["kind"] == "contract" for r in rows), "field_atoms": len(atoms),
                "fields": sum(len(r["fields"]) for r in rows), "references": sum(len(r["uses"]) for r in rows),
                "manifest_bytes": encoded_size(rows), "unit_bytes": encoded_size(batch_unit(rows))}
    for key, value in measured.items():
        if value > bounds[key]:
            raise ValueError(f"bounded batch {key} {value} exceeds policy {bounds[key]}")
    # Recompute companion/definition checks; only the DEFAULT packing-size hold
    # is irrelevant to this independently bounded policy. Never trust depends_on.
    for unit in _schedule(project, atoms):
        if any(hold != SIZE_HOLD for hold in unit["holds"]):
            raise ValueError("bounded batch manifest is not closed: " + "; ".join(unit["holds"]))
    return measured


def batch_unit(rows):
    from .plan_units import _hash, _unit_changes
    atoms = _unit_changes(rows)
    return {"id": "unit-001", "state": "pending", "changes": atoms,
            "existing_text_bytes": 0, "estimated_text_bytes": 512 * len(rows),
            "contract_count": sum(r["kind"] == "contract" for r in rows),
            "depends_on": [], "holds": [], "hash": _hash(atoms)}


def batch_schedule_errors(schedule, project=None):
    """Recheck saved policy, base evidence and actual partition, including closure."""
    admission = schedule.get("batch_admission")
    if admission is None:
        return ["missing bounded batch admission"]
    try:
        from .plan_units import _identity
        if admission["policy"] != BATCH_POLICY:
            return ["bounded batch policy/version/bounds changed"]
        base = Project(id=schedule["project_id"], name="admission", revision=admission["base_revision"],
                       **admission["base"], **admission["residue"])
        if (set(admission["residue"]) != EMPTY_RESIDUE_FIELDS or not empty_planning_base(base)
                or _identity(base) != schedule["base_fingerprint"]):
            return ["bounded batch admission base is not the pinned empty plan"]
        validate_batch_manifest(base, schedule["manifest"])
        expected = batch_unit(schedule["manifest"])
        units = schedule["units"]
        if len(units) != 1 or any(units[0].get(k) != v for k, v in expected.items() if k != "state"):
            return ["bounded batch unit bounds, references or partition changed"]
        if units[0]["state"] not in {"pending", "completed"}:
            return ["bounded batch unit is not admissible"]
        if project is not None and not schedule["completed_ids"] and not empty_planning_base(project):
            return ["bounded batch pending base is no longer empty"]
        return []
    except (KeyError, TypeError, ValueError, AttributeError):
        return ["malformed bounded batch admission"]


def validate_batch_delta(schedule, pin, raw, actual):
    from .plan_units import _key
    if pin.get("batch_admission") != schedule["batch_admission"]:
        raise ValueError("bounded batch policy/base pin changed")
    bounds = BATCH_POLICY["bounds"]
    if encoded_size(raw) > bounds["delta_bytes"]:
        raise ValueError("bounded batch actual delta bytes exceed policy")
    if sum(len(r["fields"]) for r in actual.values()) > bounds["fields"]:
        raise ValueError("bounded batch actual fields exceed policy")
    references = [u for r in actual.values() for u in r["uses"]]
    # Manifest `provides` references define capability identities. Runtime
    # consumption is nested metadata, but every edge still spends the actual
    # closed-batch reference budget and must target this admitted cohort.
    references.extend({"kind": "capability", "id": key}
                      for contract in raw.get("contracts", [])
                      for capability in contract.get("provides") or []
                      for key in capability.get("consumes") or [])
    if len(references) > bounds["references"]:
        raise ValueError("bounded batch actual references exceed policy")
    definitions = {_key(r["kind"], r["id"]) for r in schedule["manifest"]}
    definitions |= {_key("capability", u["id"]) for r in schedule["manifest"]
                    for u in r["uses"] if u["field"] == "provides"}
    if any(_key(u["kind"], u["id"]) not in definitions for u in references):
        raise ValueError("bounded batch actual reference is outside its closed manifest")


def select_experiment(argument, project, previous, resume):
    """Application-only opt-in; not read from project settings or model arguments."""
    if argument is None:
        if resume and previous.get("planning_experiment"):
            raise ValueError("saved experimental candidate needs an explicit supported stage")
        return None
    if (not isinstance(argument, dict) or set(argument) != {"project_id", "version"}
            or argument["project_id"] != project.id):
        raise ValueError("planning experiment project/stage mismatch")
    selected = {"project_id": project.id, **experiment_policy(argument["version"])}
    if selected["version"] == COLD_START_EXPERIMENT:
        if resume or not empty_planning_base(project):
            raise ValueError("cold-start experiment requires a new strictly empty planning base")
    else:
        if (not resume or previous.get("planning_experiment", {}).get("version") != COLD_START_EXPERIMENT
                or not previous.get("reviews") or not previous.get("report", {}).get("semantic_batch", {}).get("issues")):
            raise ValueError("repair experiment requires a completed cold-start stage with actionable review issues")
        from .plan_units import all_units_complete
        if not all_units_complete(previous):
            raise ValueError("repair experiment requires complete cold-start generation")
    return selected


def request_stage_rejection(metrics, tools, purpose):
    """Additional cap/sequence gate immediately before dispatch, not UI cancellation."""
    selected = metrics.get("planning_experiment")
    if selected is None:
        return None
    try:
        expected = {"project_id": selected["project_id"], **experiment_policy(selected["version"])}
        if selected != expected:
            return "experiment_policy_changed"
        index = metrics["provider_calls"]
        if index >= selected["limits"]["max_calls"]:
            return "experiment_call_limit"
        names = {t["function"]["name"] for t in tools}
        wanted = selected["request_sequence"][index]
        permitted = {"router": {"schedule_plan_changes", "ask_user"},
                     "generation": {"submit_plan_delta", "ask_user"},
                     "semantic_review": {"submit_plan_review"}}[wanted]
        required = {"router": "schedule_plan_changes", "generation": "submit_plan_delta",
                    "semantic_review": "submit_plan_review"}[wanted]
        if (required not in names or names - permitted
                or (purpose == "semantic_review") != (wanted == "semantic_review")):
            return "experiment_request_sequence"
    except (KeyError, TypeError, ValueError):
        return "experiment_policy_changed"
    return None


def batch_checkpoint_provenance(before, after):
    """Capture only mutable source audit metadata; actual planning stays authoritative."""
    from .plan_units import _identity
    return {"accepted_planning_fingerprint": _identity(after),
            "base_source_audit": [{"id": source.id, "activity": deepcopy(source.activity),
                                   "message_id": source.message_id} for source in before.plan_contract.sources],
            "result_source_audit": [{"id": source.id, "activity": deepcopy(source.activity),
                                     "message_id": source.message_id} for source in after.plan_contract.sources]}


def _checkpoint_source_snapshot(project, source_audit):
    """Reconstruct the historical full hash from current exact planning + audit metadata."""
    if [s.id for s in project.plan_contract.sources] != [s["id"] for s in source_audit]:
        raise ValueError("checkpoint source identities changed")
    result = project.model_copy(deep=True)
    for source, audit in zip(result.plan_contract.sources, source_audit):
        if set(audit) != {"id", "activity", "message_id"}:
            raise ValueError("invalid checkpoint source audit")
        source.activity, source.message_id = deepcopy(audit["activity"]), audit["message_id"]
    return result


def batch_record_errors(record, project=None):
    """Bind closure to actual planning, source, policy and the exact compiler audit."""
    from ..domain.plan_contracts import candidate_hash
    from .plan_ir import PlanDelta, compile_plan_delta
    from .plan_patch import _completed_compiler_audit
    from .plan_units import _delta_rows, _hash, _identity
    schedule = record["work_units"]
    try:
        project = project or Project.model_validate(record["project"])
        admission = schedule["batch_admission"]
        for checkpoint in schedule["checkpoints"]:
            if checkpoint.get("batch_admission_hash") != _hash(admission):
                return ["bounded batch checkpoint policy changed"]
            if (checkpoint["source_id"] != schedule["origin_source_id"]
                    or checkpoint["source_id"] != schedule["source_id"]
                    or checkpoint["source_id"] != record.get("turn_id", record.get("id"))
                    or checkpoint["source_id"] not in {s.id for s in project.plan_contract.sources}
                    or checkpoint["before_revision"] != admission["base_revision"]
                    or checkpoint["after_revision"] != admission["base_revision"] + 1
                    or checkpoint["after_revision"] != project.revision):
                return ["bounded batch checkpoint source/revision identity changed"]
            if (project.id != schedule["project_id"]
                    or project.model_dump(mode="json", include=EMPTY_RESIDUE_FIELDS) != admission["residue"]
                    or _identity(project) != checkpoint["accepted_planning_fingerprint"]):
                return ["bounded batch accepted planning snapshot changed"]
            base = Project(id=project.id, name="admission", revision=admission["base_revision"],
                           **admission["base"], **admission["residue"])
            before = _checkpoint_source_snapshot(base, checkpoint["base_source_audit"])
            after = _checkpoint_source_snapshot(project, checkpoint["result_source_audit"])
            if (not empty_planning_base(before) or _identity(before) != schedule["base_fingerprint"]
                    or candidate_hash(after) != checkpoint["candidate_hash"]):
                return ["bounded batch checkpoint historical snapshot hash changed"]
            audits = [a for a in record.get("compilations", [])
                      if _hash(a.get("ir")) == checkpoint["delta_hash"]
                      and a.get("base_revision") == checkpoint["before_revision"]
                      and a.get("result_revision") == checkpoint["after_revision"]]
            if not audits:
                return ["bounded batch checkpoint has no matching compiler audit"]
            audit = audits[0]
            if (audit.get("audit_hash") != _hash({k: v for k, v in audit.items() if k != "audit_hash"})
                    or audit.get("result_candidate_hash") != candidate_hash(after)):
                return ["bounded batch compiler result/audit hash changed"]
            raw, actual = _delta_rows(PlanDelta.model_validate(audit["ir"]), fragmented=True)
            compiled = compile_plan_delta(before, raw)
            if any(audit.get(key) != value for key, value in compiled.audit.items()):
                return ["bounded batch compiler project/base/compiled identity changed"]
            expected_audit = _completed_compiler_audit(compiled.audit, before, after, changed=True)
            if audit != expected_audit:
                return ["bounded batch compiler project/base/result audit identity changed"]
            validate_batch_delta(schedule, {"batch_admission": admission}, raw, actual)
            if checkpoint["submitted_fields"] != {k: sorted(r["fields"]) for k, r in actual.items()}:
                return ["bounded batch checkpoint actual field coverage changed"]
        return []
    except (KeyError, TypeError, ValueError, AttributeError):
        return ["invalid bounded batch checkpoint audit"]
