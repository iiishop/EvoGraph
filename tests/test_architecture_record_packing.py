"""Same-record packing keeps exact fields, dependency order and saved checkpoints."""
from copy import deepcopy

import pytest
from evograph.application.plan_ir import PlanDelta, submit_plan_delta
from evograph.application.plan_units import (
    MAX_UNIT_BYTES,
    MAX_UNIT_CONTRACTS,
    MAX_UNIT_OPERATIONS,
    SIZE_HOLD,
    SchedulePlanChanges,
    _hash,
    _json,
    _objects,
    _operation_count,
    _partition_errors,
    all_units_complete,
    prepare_unit_request,
    resume_unit_schedule,
    schedule_findings,
    schedule_plan_changes,
    validate_unit_delta,
)
from evograph.domain.plan_contracts import IntentSource
from test_architecture_unit_bootstrap import manifest_for, schedule
from test_plan_patch import INPUT, architecture, initial, stage

FIELDS = ["technologies", "decisions", "quality_scenarios", "risks", "architecture_summary"]
PAYLOAD = {
    "technologies": [{"area": "runtime", "choice": "Python", "rationale": "A local parser"}],
    "decisions": ["Keep the parser read-only"],
    "quality_scenarios": [],
    "risks": ["Invalid links need actionable reports"],
    "architecture_summary": "Local parser and reporting modules",
}


def sized_architecture(app, size=6295):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core", "aux"))
    latest = db.project.architectures[-1]
    latest.risks = [""]
    latest.risks = ["x" * (size - len(_json(latest.model_dump()).encode()))]
    assert len(_json(latest.model_dump()).encode()) == size
    db.record["project"] = db.project.model_dump()
    db.record = db.store.save(db.record)
    return ctx, db


def metadata(fields=FIELDS, uses=()):
    return {"kind": "architecture", "id": "architecture", "fields": list(fields),
            "uses": list(uses), "intent": "Clarify existing architecture"}


@pytest.mark.parametrize("size", [MAX_UNIT_BYTES - 1, MAX_UNIT_BYTES, MAX_UNIT_BYTES + 1, 6295])
def test_metadata_atoms_share_one_canonical_record_size(app, size):
    ctx, db = sized_architecture(app, size)
    saved = schedule(ctx, [metadata()])
    assert len(saved["units"]) == 1
    unit = saved["units"][0]
    assert unit["state"] == "pending" and unit["holds"] == []
    assert unit["existing_text_bytes"] == unit["estimated_text_bytes"] == size
    assert _operation_count(unit["changes"]) == 1
    assert [r["fields"] for r in unit["changes"]] == [[field] for field in FIELDS]
    assert all(r["origin_change_hash"] == _hash(saved["manifest"][0]) for r in unit["changes"])
    assert _partition_errors(saved) == schedule_findings(db.record, db.project) == []


@pytest.mark.parametrize("combined_size", [MAX_UNIT_BYTES - 1, MAX_UNIT_BYTES, MAX_UNIT_BYTES + 1])
@pytest.mark.parametrize("architecture_first", [True, False])
def test_distinct_records_obey_exact_combined_size_boundary(app, combined_size, architecture_first):
    ctx, db = sized_architecture(app)
    component_bytes = len(_json(_objects(db.project)["component:core"]).encode())
    latest = db.project.architectures[-1]
    latest.risks[0] = latest.risks[0][:len(latest.risks[0]) - (6295 + component_bytes - combined_size)]
    db.record["project"] = db.project.model_dump()
    component = {"kind": "component", "id": "core", "fields": ["description"],
                 "uses": [], "intent": "Clarify responsibility"}
    rows = [metadata(), component] if architecture_first else [component, metadata()]
    saved = schedule(ctx, rows)
    assert len(saved["units"]) == (1 if combined_size <= MAX_UNIT_BYTES else 2)
    assert sum(u["existing_text_bytes"] for u in saved["units"]) == combined_size
    assert all(u["estimated_text_bytes"] <= MAX_UNIT_BYTES for u in saved["units"])
    assert _partition_errors(saved) == []


def test_oversized_record_stays_separate_from_neighbors_and_new_definition(app):
    ctx, db = sized_architecture(app)
    rows = manifest_for({"components": [{"id": "core", "label": "Core", "description": "Core parser"}]})
    rows += [metadata([*FIELDS, "architecture_milestone_ids"], [
        {"field": "architecture_milestone_ids", "kind": "slice", "id": "LATER"}])]
    rows += manifest_for({
        "components": [{"id": "aux", "label": "Auxiliary", "description": "Auxiliary parser"}],
        "slices": [{"id": "LATER", "title": "Later delivery", "intent": "Report links", "scope": ["report.py"]}],
        "contracts": [{"key": "later", "owner": "LATER", "statement": "Report links",
                       "requirement_ids": ["r"], "mechanism": "Traverse the parser result"}],
    })
    saved = schedule(ctx, rows)
    packed = next(u for u in saved["units"] if any(r["fields"] == ["technologies"] for r in u["changes"]))
    assert [r["fields"] for r in packed["changes"]] == [[field] for field in FIELDS]
    assert packed["estimated_text_bytes"] == 6295
    definition = next(u for u in saved["units"] if any(r["id"] == "LATER" for r in u["changes"]))
    milestone = saved["units"][-1]
    assert [r["fields"] for r in milestone["changes"]] == [["architecture_milestone_ids"]]
    assert milestone["depends_on"] == [definition["id"]]
    assert saved["units"].index(packed) < saved["units"].index(definition)
    assert all(u["estimated_text_bytes"] <= MAX_UNIT_BYTES or _operation_count(u["changes"]) == 1
               for u in saved["units"])
    assert schedule_findings(db.record, db.project) == []


def test_atomic_distinct_records_remain_held_above_size_target(app):
    ctx, db = sized_architecture(app)
    binding = db.project.plan_contract.bindings[0]
    binding.mechanism = "x" * MAX_UNIT_BYTES
    db.record["project"] = db.project.model_dump()
    rows = manifest_for({
        "requirements": [{"id": "new", "quote": INPUT, "kind": "outcome", "source_id": db.source_id}],
        "contracts": [{"key": "check", "requirement_ids": ["r", "new"]}],
    })
    saved = schedule(ctx, rows)
    assert len(saved["units"]) == 1
    unit = saved["units"][0]
    assert unit["estimated_text_bytes"] > MAX_UNIT_BYTES
    assert _operation_count(unit["changes"]) == 2
    assert unit["state"] == "held" and unit["holds"] == [SIZE_HOLD]
    assert prepare_unit_request(db)["current_unit"] is None


@pytest.mark.parametrize("kind,limit", [("components", MAX_UNIT_OPERATIONS), ("contracts", MAX_UNIT_CONTRACTS)])
def test_other_packing_caps_are_unchanged(app, kind, limit):
    ctx, db = sized_architecture(app)
    values = ([{"id": f"c{i}", "label": f"Component {i}", "description": "New module"} for i in range(limit + 1)]
              if kind == "components" else
              [{"key": f"c{i}", "owner": "CHECK", "statement": f"Report {i}", "requirement_ids": ["r"],
                "mechanism": "Traverse parser results"} for i in range(limit + 1)])
    saved = schedule(ctx, manifest_for({kind: values}))
    assert len(saved["units"]) == 2
    assert [len(u["changes"]) for u in saved["units"]] == [limit, 1]
    assert all(u["contract_count"] <= MAX_UNIT_CONTRACTS
               and _operation_count(u["changes"]) <= MAX_UNIT_OPERATIONS for u in saved["units"])
    assert schedule_findings(db.record, db.project) == []


def test_packed_fields_require_exact_submission_and_one_complete_checkpoint(app):
    ctx, db = sized_architecture(app)
    before_architecture = db.project.architectures[-1].model_dump()
    db.segmented_planning = True
    schedule(ctx, [metadata()])
    unit = prepare_unit_request(db)["current_unit"]
    before = deepcopy(db.record)
    for omitted in FIELDS:
        with pytest.raises(ValueError, match="exactly current unit identities"):
            submit_plan_delta(ctx, PlanDelta.model_validate({k: v for k, v in PAYLOAD.items() if k != omitted}))
    with pytest.raises(ValueError, match="exactly current unit identities"):
        submit_plan_delta(ctx, PlanDelta.model_validate({**PAYLOAD, "architecture_milestone_ids": ["CHECK"]}))
    assert db.record == before
    for pin_field in ("unit_hash", "manifest_hash"):
        db.record["unit_request"][pin_field] = "altered"
        with pytest.raises(ValueError, match="pin changed"):
            validate_unit_delta(db, PAYLOAD)
        db.record = deepcopy(before)
    submit_plan_delta(ctx, PlanDelta.model_validate(PAYLOAD))
    assert all_units_complete(db.record)
    assert db.project.architectures[0].model_dump() == before_architecture
    checkpoints = db.record["work_units"]["checkpoints"]
    assert len(checkpoints) == 1
    assert checkpoints[0]["change_ids"] == [r["change_id"] for r in unit["changes"]]
    assert checkpoints[0]["submitted_fields"] == {r["change_id"]: r["fields"] for r in unit["changes"]}
    assert validate_unit_delta(db, PAYLOAD) is True
    with pytest.raises(ValueError, match="already completed"):
        validate_unit_delta(db, {**PAYLOAD, "risks": ["Different replay"]})
    checkpoints[0]["submitted_fields"].pop("architecture:architecture#risks")
    assert not all_units_complete(db.record)
    assert schedule_findings(db.record, db.project)[0]["code"] == "invalid_unit_schedule"


@pytest.mark.parametrize("completed", [False, True])
def test_saved_split_schedule_is_never_repacked_on_repeat_or_resume(app, completed):
    ctx, db = sized_architecture(app)
    db.segmented_planning = True
    saved = schedule(ctx, [metadata()])
    template = deepcopy(saved["units"][0])
    saved["units"] = [{**deepcopy(template), "id": f"unit-{i + 1:03d}", "changes": [row], "hash": _hash([row])}
                      for i, row in enumerate(template["changes"])]
    saved["pending_ids"] = [u["id"] for u in saved["units"]]
    prepare_unit_request(db)
    if completed:
        submit_plan_delta(ctx, PlanDelta.model_validate({FIELDS[0]: PAYLOAD[FIELDS[0]]}))
    before = deepcopy(db.record)
    result = schedule_plan_changes(ctx, SchedulePlanChanges.model_validate({"changes": [metadata()]}))
    assert result["status"] == "NO_PROGRESS" and db.record == before
    candidate = db.project.model_copy(deep=True)
    candidate.plan_contract.sources.append(IntentSource(id="resume", text=INPUT))
    resumed = resume_unit_schedule(db.record, candidate, "resume", INPUT)
    assert resumed is not None
    for key in ("units", "manifest", "manifest_hash", "checkpoints", "pending_ids", "completed_ids"):
        assert resumed[key] == before["work_units"][key]
    assert _partition_errors(resumed) == []
