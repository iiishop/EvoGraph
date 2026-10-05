"""Offline schema coverage; schemas never substitute for server admission."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from evograph.application.plan_ir import DELTA_TOOL, PlanDelta, compile_plan_delta
from evograph.application.plan_unit_schema import project_unit_schema, unit_delta_tool_for
from evograph.application.plan_units import (
    _hash,
    prepare_unit_request,
    schedule_findings,
    validate_unit_delta,
)
from test_architecture_unit_bootstrap import manifest_for, schedule
from test_plan_ir import component, contract, slice_, submit
from test_plan_patch import INPUT, architecture, behavior, initial, node, stage


def assigned(app, payload, *, setup=None):
    ctx, db = stage(app)
    initial(ctx, architecture=setup or architecture("core", "unchanged"))
    db.segmented_planning = True
    schedule(ctx, manifest_for(payload))
    assert not schedule_findings(db.record, db.project)
    assert len(db.record["work_units"]["units"]) == 1
    prepare_unit_request(db)
    return ctx, db


def projection(db):
    before = deepcopy(db.record)
    schema, audit = project_unit_schema(db.record, db.project)
    assert db.record == before
    assert audit["status"] == "projected", audit
    unit = db.record["work_units"]["units"][0]
    assert set(audit["covered_change_ids"]) == {row["change_id"] for row in unit["changes"]}
    assert schema["additionalProperties"] is False
    return schema


def branches(schema, field):
    items = schema["properties"][field]["items"]
    return items.get("anyOf", [items])


def test_create_and_update_share_family_without_forcing_existing_fields(app):
    payload = {"components": [{"id": "core", "description": "Refined responsibility"}, component("new")],
               "contracts": [{"key": "check", "statement": "Updated result"}, contract("new", "CHECK")]}
    _, db = assigned(app, payload)
    schema = projection(db)
    for field, identity, existing, new, creation_fields in (
        ("components", "id", "core", "new", {"id", "label", "description"}),
        ("contracts", "key", "check", "new", {"key", "owner", "statement", "requirement_ids", "mechanism"}),
    ):
        old, created = branches(schema, field)
        assert old["properties"][identity]["enum"] == [existing]
        assert old["required"] == [identity]
        assert created["properties"][identity]["enum"] == [new]
        assert set(created["required"]) == creation_fields
        assert set(old["properties"]) == set(created["properties"])
        assert schema["properties"][field]["minItems"] == schema["properties"][field]["maxItems"] == 2
    validate_unit_delta(db, payload)
    compile_plan_delta(db.project, payload)


def test_same_record_mechanism_owner_and_outside_unit_references_stay_legal(app):
    _, db = assigned(app, {"contracts": [{"key": "check", "statement": "Updated result"}]})
    schema = projection(db)
    fields = branches(schema, "contracts")[0]["properties"]
    original = PlanDelta.model_json_schema()["$defs"]["ContractDelta"]["properties"]
    assert {key: value for key, value in fields.items() if key != "key"} == {
        key: value for key, value in original.items() if key != "key"}
    assert {"mechanism", "owner", "owner_change_reason", "provides", "steps"} <= fields.keys()
    payload = {"contracts": [{"key": "check", "mechanism": "Uses the unchanged parser component",
                 "owner": "CHECK", "component_ids": ["unchanged"], "requirement_ids": ["r"]}]}
    validate_unit_delta(db, payload)
    compile_plan_delta(db.project, payload)
    assert set(schema["properties"]) == {"summary", "contracts", "restore_contract_keys"}
    assert schema["properties"]["restore_contract_keys"]["items"]["enum"] == ["check"]


def test_mixed_creation_families_and_required_architecture_fields(app):
    ctx, db = stage(app)
    db.segmented_planning = True
    payload = {"target": INPUT, "requirements": [{"id": "r", "quote": INPUT, "kind": "outcome"}],
        "process_constraints": [{"id": "process", "quote": INPUT, "rule": "planning_only"}],
        "slices": [slice_()], "contracts": [contract()], "components": [component("core")],
        "architecture_summary": "Read-only local modules",
        "technologies": [{"area": "runtime", "choice": "Python", "rationale": "Local operation"}]}
    schedule(ctx, manifest_for(payload))
    assert len(db.record["work_units"]["units"]) == 1
    prepare_unit_request(db)
    schema = projection(db)
    assert set(schema["required"]) == set(payload)
    assert {"id", "title", "intent", "scope"} == set(branches(schema, "slices")[0]["required"])
    assert {"id", "quote", "kind"} == set(branches(schema, "requirements")[0]["required"])
    validate_unit_delta(db, payload)
    compile_plan_delta(db.project, payload)


def test_removals_retirements_and_relation_triples_have_complete_coverage(app):
    payload = {"remove_contract_keys": ["check"], "remove_slice_ids": ["CHECK"],
        "retire_requirements": [{"id": "r", "quote": INPUT, "reason": "Retire old scope"}],
        "remove_component_ids": ["core"],
        "retirements": [{"component_id": "core", "milestone_id": "NEXT", "instruction": "Migrate old parser"}],
        "requirements": [{"id": "r2", "quote": INPUT, "kind": "outcome"}],
        "contracts": [{"key": "next", "requirement_ids": ["r2"]}],
        "remove_relations": [{"source": "core", "target": "unchanged", "label": "reads"}]}
    setup = architecture("core", "unchanged")
    setup["diagram"]["edges"] = [{"source": "core", "target": "unchanged", "label": "reads"}]
    ctx, db = stage(app)
    initial(ctx, architecture=setup, milestones=[node(), node("NEXT")])
    db.segmented_planning = True
    schedule(ctx, manifest_for(payload))
    assert not schedule_findings(db.record, db.project)
    assert len(db.record["work_units"]["units"]) == 1
    prepare_unit_request(db)
    schema = projection(db)
    assert set(schema["required"]) == set(payload)
    for field, identity in (("remove_contract_keys", "check"), ("remove_slice_ids", "CHECK"),
                            ("remove_component_ids", "core")):
        assert schema["properties"][field]["items"]["enum"] == [identity]
    assert branches(schema, "retirements")[0]["properties"]["component_id"]["enum"] == ["core"]
    assert branches(schema, "retire_requirements")[0]["properties"]["id"]["enum"] == ["r"]
    validate_unit_delta(db, payload)


def test_relation_branches_keep_exact_combinations_and_separate_removals(app):
    rows = [{"source": "core", "target": "unchanged", "label": "calls"},
            {"source": "unchanged", "target": "core", "label": "replies"}]
    _, db = assigned(app, {"relations": rows, "remove_relations": [rows[0]]})
    schema = projection(db)
    def identities(field):
        return [tuple(branch["properties"][key]["enum"][0] for key in ("source", "target", "label"))
                for branch in branches(schema, field)]
    assert identities("relations") == [("core", "unchanged", "calls"), ("unchanged", "core", "replies")]
    assert identities("remove_relations") == [("core", "unchanged", "calls")]
    assert ("core", "unchanged", "replies") not in identities("relations")
    assert "RelationDelta" not in schema.get("$defs", {})


def test_restoration_remains_available_with_creation_fields(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node(behaviors=[behavior("check"), behavior("step", acceptance_scope="milestone")])])
    submit(ctx, {"remove_contract_keys": ["step"]})
    db.segmented_planning = True
    payload = {"contracts": [contract("step")], "restore_contract_keys": ["step"]}
    schedule(ctx, manifest_for(payload))
    prepare_unit_request(db)
    schema = projection(db)
    assert schema["properties"]["restore_contract_keys"]["items"]["enum"] == ["step"]
    assert set(branches(schema, "contracts")[0]["required"]) == {
        "key", "owner", "statement", "requirement_ids", "mechanism"}
    validate_unit_delta(db, payload)
    compile_plan_delta(db.project, payload)


def test_legacy_architecture_hints_do_not_become_field_restrictions(app):
    _, db = assigned(app, {"decisions": ["New decision"]})
    saved = db.record["work_units"]
    saved["version"] = "plan-units/v1"
    saved["units"][0]["changes"] = deepcopy(saved["manifest"])
    saved["units"][0]["hash"] = _hash(saved["units"][0]["changes"])
    prepare_unit_request(db)
    schema = projection(db)
    assert {"architecture_summary", "technologies", "risks", "quality_scenarios"} <= schema["properties"].keys()
    validate_unit_delta(db, {"risks": ["A same-record repair omitted from routing hints"]})


@pytest.mark.parametrize("damage", ["no_schedule", "no_pin", "manifest", "unit", "pin", "revision", "completed"])
def test_untrusted_or_missing_assignment_falls_back_whole_not_partial(app, damage):
    _, db = assigned(app, {"components": [{"id": "core", "description": "Updated"}]})
    if damage == "no_schedule":
        db.record.pop("work_units")
    elif damage == "no_pin":
        db.record.pop("unit_request")
    elif damage == "manifest":
        db.record["work_units"]["manifest"][0]["id"] = "outside"
    elif damage == "unit":
        db.record["work_units"]["units"][0]["changes"][0]["id"] = "outside"
    elif damage == "pin":
        db.record["unit_request"]["unit_hash"] = "changed"
    elif damage == "revision":
        db.record["unit_request"]["revision"] -= 1
    else:
        db.record["unit_request"]["accepted_delta_hash"] = "already accepted"
    schema, audit = project_unit_schema(db.record, db.project)
    assert schema == DELTA_TOOL.schema()["function"]["parameters"]
    assert audit["status"] == "fallback_full_schema" and audit["reason"]
    assert audit["covered_change_ids"] == []
    with pytest.raises(ValueError):
        validate_unit_delta(db, {"components": [{"id": "core", "description": "Updated"}]})


def test_unsupported_projection_falls_back_and_records_reason(app, monkeypatch):
    _, db = assigned(app, {"components": [{"id": "core", "description": "Updated"}]})
    def unsupported(*args):
        raise ValueError("unsupported collection shape")
    monkeypatch.setattr("evograph.application.plan_unit_schema._object_items", unsupported)
    tool = unit_delta_tool_for(db)
    assert tool.schema() == DELTA_TOOL.schema()
    assert db.record["tool_schema_projections"][-1]["reason"] == "unsupported collection shape"
    assert tool.name == DELTA_TOOL.name and tool.handler is DELTA_TOOL.handler
    assert tool.parameters is PlanDelta


def test_uncataloged_future_operation_is_not_silently_removed(app, monkeypatch):
    _, db = assigned(app, {"components": [{"id": "core", "description": "Updated"}]})
    original = PlanDelta.model_json_schema()
    original["properties"]["future_operation"] = {"type": "string"}
    monkeypatch.setattr(PlanDelta, "model_json_schema", classmethod(lambda cls: deepcopy(original)))
    schema, audit = project_unit_schema(db.record, db.project)
    assert schema == original
    assert audit["status"] == "fallback_full_schema"
    assert audit["reason"] == "uncataloged PlanDelta operation fields"


def test_schema_snapshot_and_fallback_never_weaken_atomic_server_admission(app):
    ctx, db = assigned(app, {"components": [{"id": "core", "description": "Updated"}]})
    base = DELTA_TOOL.schema()
    tool = unit_delta_tool_for(db)
    assert tool.parameters is PlanDelta and tool.handler is DELTA_TOOL.handler
    schema = tool.schema()
    schema["function"]["parameters"]["properties"].clear()
    assert tool.schema()["function"]["parameters"]["properties"]
    assert DELTA_TOOL.schema() == base
    before = _hash(db.record), db.project.model_dump(), deepcopy(db.saved_events)
    payload = {"components": [{"id": "core", "description": "Updated"}],
               "relations": [{"source": "core", "target": "unchanged", "label": "unassigned"}]}
    with pytest.raises(ValueError, match="submit exactly current unit identities"):
        tool.handler(ctx, tool.parameters.model_validate(payload))
    assert (_hash(db.record), db.project.model_dump(), db.saved_events) == before


def test_parallel_project_projections_do_not_share_an_allowlist(app):
    _, first = assigned(app, {"components": [{"id": "core", "description": "Updated"}]})
    _, second = assigned(app, {"components": [component("other-project-only")]})
    original = DELTA_TOOL.schema()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda db: project_unit_schema(db.record, db.project), (first, second)))
    schemas = [result[0] for result in results]
    assert all(result[1]["status"] == "projected" for result in results)
    assert branches(schemas[0], "components")[0]["properties"]["id"]["enum"] == ["core"]
    assert branches(schemas[1], "components")[0]["properties"]["id"]["enum"] == ["other-project-only"]
    schemas[0]["properties"].clear()
    assert project_unit_schema(second.record, second.project)[0] == schemas[1]
    assert DELTA_TOOL.schema() == original
