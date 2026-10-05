"""QA54 cold-start regression: scheduling/compilation only, never semantic quality."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_ir import PlanDelta, compile_plan_delta, submit_plan_delta
from evograph.application.plan_units import (
    ARCHITECTURE_KINDS,
    BOOTSTRAP_FIELDS,
    MAX_UNIT_OPERATIONS,
    SIZE_HOLD,
    SchedulePlanChanges,
    _delta_rows,
    _hash,
    _operation_count,
    _partition_errors,
    all_units_complete,
    current_unit_context,
    prepare_unit_request,
    resume_unit_schedule,
    schedule_findings,
    schedule_plan_changes,
    validate_unit_delta,
)
from evograph.domain.plan_contracts import IntentSource, contract_findings
from test_plan_patch import INPUT, architecture, initial, stage


@pytest.fixture
def live_manifest():
    return json.loads((Path(__file__).parent / "fixtures/qa54_cold_start_manifest.json").read_text())


def schedule(ctx, changes):
    args = SchedulePlanChanges.model_validate({"changes": [
        {key: row[key] for key in ("kind", "id", "fields", "uses", "intent")} for row in changes]})
    schedule_plan_changes(ctx, args)
    return ctx.application.db.record["work_units"]


def manifest_for(delta):
    rows = []
    for key, value in _delta_rows(PlanDelta.model_validate(delta))[1].items():
        kind, identity = key.split(":", 1)
        rows.append({"kind": kind, "id": identity, "fields": sorted(value["fields"]),
                     "uses": value["uses"], "intent": "Deterministic structural regression"})
    return rows


def valid_delta(source_id):
    return {
        "target": INPUT,
        "requirements": [{"id": "r", "quote": INPUT, "kind": "outcome", "source_id": source_id}],
        "slices": [{"id": "CHECK", "title": "Local checker", "intent": "Report broken links", "scope": ["parser.py"]}],
        "contracts": [{"key": "links", "owner": "CHECK", "statement": "Report broken Markdown links",
                       "requirement_ids": ["r"], "mechanism": "Traverse the local Markdown AST without writing files",
                       "component_ids": ["parser", "cli"]}],
        "components": [{"id": cid, "label": cid, "description": "Owns " + cid} for cid in
                       ("parser", "cli", "report", "scanner", "paths")],
        "architecture_summary": "A local read-only link checker",
        "technologies": [{"area": "runtime", "choice": "Python", "rationale": "Local execution"}],
        "architecture_groups": [{"id": "modules", "label": "Checker", "member_node_ids": ["parser", "cli"]}],
        "architecture_milestone_ids": ["CHECK"],
        "decisions": ["Keep source files read-only"],
        "risks": ["Paths can be invalid"],
    }


def payload_for(unit, full):
    rows = _delta_rows(PlanDelta.model_validate(full))[1]
    result = {}
    for change in unit["changes"]:
        kind, identity = change["kind"], change["id"]
        if kind == "architecture":
            result.update({field: deepcopy(full[field]) for field in change["fields"]})
        elif kind == "target":
            result["target"] = full["target"]
        else:
            field, id_field = {"requirement": ("requirements", "id"), "slice": ("slices", "id"),
                               "contract": ("contracts", "key"), "component": ("components", "id")}[kind]
            assert change["change_id"] in rows
            result.setdefault(field, []).extend(deepcopy(item) for item in full[field] if item[id_field] == identity)
    return result


def test_live_manifest_partitions_bootstrap_without_hiding_missing_acceptance(app, live_manifest):
    ctx, db = stage(app)
    saved = schedule(ctx, live_manifest["manifest"])
    assert saved["manifest"] == live_manifest["manifest"]
    assert saved["manifest_hash"] == _hash(live_manifest["manifest"])
    assert not _partition_errors(saved)
    assert all(_operation_count(u["changes"]) <= MAX_UNIT_OPERATIONS for u in saved["units"])
    assert all(SIZE_HOLD not in u["holds"] for u in saved["units"])
    bootstrap = next(u for u in saved["units"] if any(
        "architecture_summary" in r["fields"] for r in u["changes"]))
    assert BOOTSTRAP_FIELDS <= {f for r in bootstrap["changes"] if r["kind"] == "architecture" for f in r["fields"]}
    assert any(r["kind"] == "component" for r in bootstrap["changes"])
    assert not any("architecture_milestone_ids" in r["fields"] for r in bootstrap["changes"])
    for unit in saved["units"]:
        if unit != bootstrap and any(r["kind"] in ARCHITECTURE_KINDS for r in unit["changes"]):
            assert bootstrap["id"] in unit["depends_on"]
    held = [u for u in saved["units"] if u["state"] == "held"]
    assert len(held) == 1
    assert [r["id"] for r in held[0]["changes"]] == ["slice-m3-single-server-release"]
    assert held[0]["holds"] == ["new slice needs its first covering/owned contract"]
    assert [f["code"] for f in schedule_findings(db.record, db.project)] == ["work_units_held"]
    assert prepare_unit_request(db)["current_unit"] is None
    assert current_unit_context(db)["current_unit"] is None
    assert db.record["unit_request"] is None


@pytest.mark.parametrize("omit", ["architecture", "component"])
def test_missing_bootstrap_definition_is_held_before_generation(app, omit):
    ctx, db = stage(app)
    full = valid_delta(db.source_id)
    rows = [r for r in manifest_for(full) if r["kind"] not in {omit, "contract", "slice", "requirement"}]
    # Keep only metadata with no references when components were removed.
    rows = [r for r in rows if r["kind"] != "architecture"] + ([
        {"kind": "architecture", "id": "architecture", "fields": ["architecture_summary", "technologies"],
         "uses": [], "intent": "Bootstrap"}] if omit == "component" else [])
    schedule(ctx, rows)
    assert schedule_findings(db.record, db.project)
    assert prepare_unit_request(db)["current_unit"] is None


def test_missing_required_metadata_rejected_before_atomization(app):
    ctx, db = stage(app)
    with pytest.raises(ValueError, match="missing=.*technologies"):
        schedule(ctx, [{"kind": "architecture", "id": "architecture", "fields": ["architecture_summary"],
                        "uses": [], "intent": "Incomplete bootstrap"}])
    assert "work_units" not in db.record


def test_saved_v1_cold_start_is_not_repacked_or_dispatched(app, live_manifest):
    ctx, db = stage(app)
    saved = schedule(ctx, live_manifest["manifest"])
    saved.update(version="plan-units/v1", units=deepcopy(live_manifest["legacy_units"]))
    saved["pending_ids"] = [u["id"] for u in saved["units"]]
    original = deepcopy(saved)
    source = "resume-source"
    candidate = db.project.model_copy(deep=True)
    candidate.plan_contract.sources.append(IntentSource(id=source, text=INPUT))
    resumed = resume_unit_schedule(db.record, candidate, source, INPUT)
    assert resumed is not None
    assert resumed["units"] == original["units"]
    assert resumed["manifest"] == original["manifest"]
    record = {**db.record, "turn_id": source, "work_units": resumed}
    codes = {f["code"] for f in schedule_findings(record, candidate)}
    assert codes == {"work_units_held", "architecture_bootstrap_unavailable"}


def test_offline_multisegment_compilation_and_checkpoint_retains_architecture(app):
    ctx, db = stage(app)
    db.segmented_planning = True
    full = valid_delta(db.source_id)
    saved = schedule(ctx, manifest_for(full))
    assert len(saved["units"]) > 1
    first_architecture = None
    for _ in range(len(saved["units"])):
        assert schedule_findings(db.record, db.project) == []
        unit = prepare_unit_request(db)["current_unit"]
        payload = payload_for(unit, full)
        compile_plan_delta(db.project, payload)
        result = submit_plan_delta(ctx, PlanDelta.model_validate(payload))
        assert result["candidate_state"] == "staged"
        if first_architecture is None and db.project.architectures:
            first_architecture = db.project.architectures[0].model_dump()
        if first_architecture is not None:
            assert db.project.architectures[0].model_dump() == first_architecture
        assert contract_findings(db.project) == []
    assert all_units_complete(db.record)
    assert len(db.record["work_units"]["checkpoints"]) == len(saved["units"])
    assert db.project.architectures[-1].diagram.milestone_ids == ["CHECK"]
    assert db.project.architectures[-1].summary == full["architecture_summary"]
    assert app.db.get(ctx.project_id).architectures == []
    # A structural checkpoint and full coverage do not fabricate semantic review.
    assert db.record.get("validation_requested") is False
    assert not db.record.get("reviews")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "field", "origin", "manifest"])
def test_incomplete_or_altered_partition_cannot_pin_or_complete(app, mutation):
    ctx, db = stage(app)
    saved = schedule(ctx, manifest_for(valid_delta(db.source_id)))
    unit = next(u for u in saved["units"] if any(r["kind"] == "architecture" for r in u["changes"]))
    fragment = next(r for r in unit["changes"] if r["kind"] == "architecture")
    if mutation == "missing":
        unit["changes"].remove(fragment)
    elif mutation == "duplicate":
        unit["changes"].append(deepcopy(fragment))
    elif mutation == "field":
        fragment["fields"].append("risks")
    elif mutation == "origin":
        fragment["origin_change_hash"] = "altered"
    else:
        saved["manifest"][-1]["intent"] = "altered original"
    unit["hash"] = _hash(unit["changes"])
    assert schedule_findings(db.record, db.project)[0]["code"] == "invalid_unit_schedule"
    assert prepare_unit_request(db)["current_unit"] is None
    assert not all_units_complete(db.record)


def test_extra_or_omitted_architecture_fields_and_stale_pin_are_rejected(app):
    ctx, db = stage(app)
    full = valid_delta(db.source_id)
    schedule(ctx, manifest_for(full))
    unit = prepare_unit_request(db)["current_unit"]
    payload = payload_for(unit, full)
    assert "architecture_milestone_ids" not in payload
    with pytest.raises(ValueError, match="exactly current unit identities"):
        validate_unit_delta(db, {**payload, "architecture_milestone_ids": ["CHECK"]})
    omitted = {key: value for key, value in payload.items() if key != "technologies"}
    with pytest.raises(ValueError, match="exactly current unit identities"):
        validate_unit_delta(db, omitted)
    db.record["unit_request"]["unit_hash"] = "stale"
    with pytest.raises(ValueError, match="unavailable or held"):
        validate_unit_delta(db, payload)


def test_existing_architecture_and_other_record_field_freedom_are_preserved(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core"))
    before = db.project.architectures[-1].model_dump()
    db.segmented_planning = True
    rows = [{"kind": "component", "id": "core", "fields": ["label"], "uses": [], "intent": "Clarify label"}]
    schedule(ctx, rows)
    unit = prepare_unit_request(db)["current_unit"]
    assert [r["kind"] for r in unit["changes"]] == ["component"]
    submit_plan_delta(ctx, PlanDelta.model_validate({"components": [
        {"id": "core", "label": "Core parser", "description": "Updated responsibility", "role": "backend"}]}))
    assert all_units_complete(db.record)
    after = db.project.architectures[-1].model_dump()
    for field in ("summary", "technologies", "decisions", "risks"):
        assert after[field] == before[field]
    assert db.project.architectures[0].model_dump() == before


def test_completed_partition_requires_every_original_field_and_matching_checkpoint(app):
    ctx, db = stage(app)
    db.segmented_planning = True
    full = valid_delta(db.source_id)
    schedule(ctx, manifest_for(full))
    while (unit := prepare_unit_request(db).get("current_unit")) is not None:
        submit_plan_delta(ctx, PlanDelta.model_validate(payload_for(unit, full)))
    assert all_units_complete(db.record)
    untouched = deepcopy(db.record)
    unit = db.record["work_units"]["units"][-1]
    fragment = next(row for row in unit["changes"] if row["kind"] == "architecture")
    unit["changes"].remove(fragment)
    unit["hash"] = _hash(unit["changes"])
    # Even a self-consistent forged checkpoint cannot remove promised metadata.
    checkpoint = db.record["work_units"]["checkpoints"][-1]
    checkpoint["unit_hash"] = unit["hash"]
    checkpoint["change_ids"] = [row["change_id"] for row in unit["changes"]]
    assert not all_units_complete(db.record)
    db.record = untouched
    checkpoint = db.record["work_units"]["checkpoints"][-1]
    checkpoint["submitted_fields"].pop(fragment["change_id"])
    assert not all_units_complete(db.record)


def test_repaired_schedule_keeps_rejected_original_manifest(app, live_manifest):
    ctx, db = stage(app)
    rejected = deepcopy(schedule(ctx, live_manifest["manifest"]))
    schedule(ctx, manifest_for(valid_delta(db.source_id)))
    assert db.record["work_unit_schedule_history"] == [rejected]
    assert schedule_findings(db.record, db.project) == []


def test_existing_source_slice_reference_remains_available_to_preflight(app):
    from evograph.domain.models import Milestone

    ctx, db = stage(app)
    db.project.source_milestones.append(Milestone(id="SRC", title="Read-only source", intent="Existing behavior", origin="source"))
    db.record["project"] = db.project.model_dump()
    full = valid_delta(db.source_id)
    full["slices"][0]["dependencies"] = ["SRC"]
    full["slices"][0]["dependency_reasons"] = {"SRC": "Existing source behavior"}
    schedule(ctx, manifest_for(full))
    assert schedule_findings(db.record, db.project) == []
    assert prepare_unit_request(db)["current_unit"] is not None


def test_safe_v1_existing_architecture_metadata_edit_still_checkpoints(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core"))
    db.segmented_planning = True
    saved = schedule(ctx, [{"kind": "architecture", "id": "architecture", "fields": ["risks"],
                           "uses": [], "intent": "Update risk"}])
    saved["version"] = "plan-units/v1"
    saved["units"][0]["changes"] = deepcopy(saved["manifest"])
    saved["units"][0]["hash"] = _hash(saved["manifest"])
    assert schedule_findings(db.record, db.project) == []
    prepare_unit_request(db)
    submit_plan_delta(ctx, PlanDelta.model_validate({"risks": ["Invalid links need actionable reports"]}))
    assert all_units_complete(db.record)
    assert db.project.architectures[-1].summary == "Local read-only checker with explicit module ownership"


@pytest.mark.parametrize("evidence", ["checkpoint", "completed_state"])
def test_inconsistent_completed_bookkeeping_cannot_rebuild_schedule(app, evidence):
    ctx, db = stage(app)
    saved = schedule(ctx, manifest_for(valid_delta(db.source_id)))
    if evidence == "checkpoint":
        saved["checkpoints"].append({"unit_id": saved["units"][0]["id"], "unit_hash": saved["units"][0]["hash"]})
    else:
        saved["units"][0]["state"] = "completed"
    assert saved["completed_ids"] == []
    assert schedule_findings(db.record, db.project)[0]["code"] == "invalid_unit_schedule"
    before = deepcopy(db.record)
    with pytest.raises(ValueError, match="completed units"):
        schedule(ctx, [{"kind": "target", "id": "target", "fields": ["target"],
                        "uses": [], "intent": "Replacement"}])
    assert db.record == before
