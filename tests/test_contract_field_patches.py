"""Field patches and server diffs against frozen real R2/model output, fully offline."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_ir import DELTA_TOOL, PlanDelta, compile_plan_delta
from evograph.application.plan_patch import contract_changes, propose_plan_patch
from evograph.domain.models import BehaviorRevision, Project
from evograph.domain.plan_contracts import active_behaviors, candidate_hash, history_findings
from test_plan_ir import complete_delta, contract, submit, unchanged_on_error
from test_plan_patch import assert_unchanged, behavior, initial, node, stage

FIXTURES = Path(__file__).parent / "fixtures"
V5 = json.loads((FIXTURES / "qa49_v5_batch_patch.json").read_text())


def json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@pytest.fixture
def canonical_r2(app):
    value = json.loads((FIXTURES / "qa49_frozen_plan.json").read_text())["project"]
    value.update(deepcopy(V5["canonical_metadata"]))
    assert json_hash(value) == V5["provenance"]["canonical_project_sha256"]
    assert json_hash(V5["model_delta"]) == V5["provenance"]["model_delta_sha256"]
    project = Project.model_validate(value)
    assert project.revision == 2
    app.db.create(project)
    return project


def assert_bound_audit(db, before, after):
    audit = db.record["compilations"][-1]
    assert audit["protocol_version"] == "plan-delta/v2"
    assert audit["project_id"] == before.id == after.id
    assert audit["base_revision"] == before.revision
    assert audit["result_revision"] == after.revision
    assert audit["base_candidate_hash"] == candidate_hash(before)
    assert audit["result_candidate_hash"] == candidate_hash(after)
    assert audit["audit_hash"] == json_hash({k: v for k, v in audit.items() if k != "audit_hash"})
    return audit


def test_real_r2_model_patch_exposes_formatter_rewrite_and_minimal_relink_preserves_text(app, canonical_r2):
    ctx, db = stage(app, canonical_r2, text=V5["input"])
    before = db.get(ctx.project_id)
    submit(ctx, deepcopy(V5["model_delta"]))
    original_changes = {item["key"]: item for item in assert_bound_audit(db, before, db.project)["contract_changes"]}
    assert set(original_changes["format-two-decimals"]["fields"]) == {"statement", "mechanism", "requirement_ids"}

    # Same actual model batch edit, changing only its needless formatter rewrite
    # into a requirement relink. The existing formatter text is not resubmitted.
    minimal = deepcopy(V5["model_delta"])
    for index, item in enumerate(minimal["contracts"]):
        if item["key"] == "format-two-decimals":
            minimal["contracts"][index] = {"key": item["key"], "requirement_ids": item["requirement_ids"]}
    ctx, db = stage(app, canonical_r2, text=V5["input"])
    before = db.get(ctx.project_id)
    canonical = app.db.get(ctx.project_id).model_dump()
    submit(ctx, minimal)
    after = db.get(ctx.project_id)
    changes = {item["key"]: item for item in assert_bound_audit(db, before, after)["contract_changes"]}
    assert set(changes) == {"argv-validation", "success-output-exit", "failure-output-exit", "format-two-decimals"}
    assert set(changes["format-two-decimals"]["fields"]) == {"requirement_ids"}
    old, new = active_behaviors(before), active_behaviors(after)
    old_bindings = {item.behavior_key: item for item in before.plan_contract.bindings}
    new_bindings = {item.behavior_key: item for item in after.plan_contract.bindings}
    formatter = new["format-two-decimals"]
    assert formatter.statement == old["format-two-decimals"].statement
    assert formatter.owner == old["format-two-decimals"].owner
    assert formatter.id != old["format-two-decimals"].id
    assert formatter.supersedes == old["format-two-decimals"].id
    assert new_bindings[formatter.behavior_key].mechanism == old_bindings[formatter.behavior_key].mechanism
    assert after.behaviors[:len(before.behaviors)] == before.behaviors
    assert history_findings(before, after) == []
    for key in old.keys() - changes.keys():
        assert new[key] == old[key]
        assert new_bindings[key] == old_bindings[key]
    assert app.db.get(ctx.project_id).model_dump() == canonical
    # This evidence test must not silently repair or claim execution of the old parser.
    assert "int(s, 10)" in new_bindings["argv-validation"].mechanism


def test_real_r2_statement_only_change_creates_revision_without_rewriting_mechanism(app, canonical_r2):
    ctx, db = stage(app, canonical_r2)
    before = db.get(ctx.project_id)
    key = "format-two-decimals"
    statement = active_behaviors(before)[key].statement + " 明确保持此格式契约。"
    submit(ctx, {"contracts": [{"key": key, "statement": statement}]})
    changes = assert_bound_audit(db, before, db.project)["contract_changes"]
    assert len(changes) == 1 and changes[0]["key"] == key
    assert changes[0]["fields"] == {"statement": {"before": active_behaviors(before)[key].statement, "after": statement}}
    assert active_behaviors(db.project)[key].supersedes == active_behaviors(before)[key].id
    assert next(b for b in db.project.plan_contract.bindings if b.behavior_key == key).mechanism == next(
        b for b in before.plan_contract.bindings if b.behavior_key == key
    ).mechanism


def test_real_r2_explicit_owner_move_preserves_text_and_original_revision_history(app, canonical_r2):
    ctx, db = stage(app, canonical_r2)
    before = db.get(ctx.project_id)
    key = "format-two-decimals"
    submit(ctx, {"contracts": [{"key": key, "owner": "slice-02-cli",
                                "owner_change_reason": "Move the formatting acceptance to the CLI delivery slice"}]})
    after = db.get(ctx.project_id)
    changes = assert_bound_audit(db, before, after)["contract_changes"]
    assert len(changes) == 1 and changes[0]["key"] == key
    assert changes[0]["fields"] == {"owner": {"before": "slice-01-core", "after": "slice-02-cli"}}
    previous, current = active_behaviors(before), active_behaviors(after)
    assert current[key].statement == previous[key].statement
    assert current[key].supersedes == previous[key].id
    assert after.behaviors[:len(before.behaviors)] == before.behaviors
    assert history_findings(before, after) == []
    for unchanged in previous.keys() - {key}:
        assert current[unchanged] == previous[unchanged]


def test_existing_key_only_is_true_noop_and_audit_has_no_invented_changes(app):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    before = db.get(ctx.project_id)
    checkpoints = len(db.saved_events)
    assert submit(ctx, {"contracts": [{"key": "cli"}]})["status"] == "NO_PROGRESS"
    assert db.project == before
    assert len(db.saved_events) == checkpoints
    assert assert_bound_audit(db, before, db.project)["contract_changes"] == []


def test_add_and_remove_contract_diff_records_match_actual_active_sets(app):
    ctx, db = stage(app)
    initial(ctx)
    before = db.get(ctx.project_id)
    submit(ctx, {"contracts": [contract("extra")]})
    after = db.get(ctx.project_id)
    addition = assert_bound_audit(db, before, after)["contract_changes"]
    assert len(addition) == 1 and addition[0]["operation"] == "added"
    assert addition[0]["before_revision_id"] is None
    assert addition[0]["after_revision_id"] == active_behaviors(after)["extra"].id
    assert addition[0]["before_owner"] is None and addition[0]["after_owner"] == "CHECK"
    assert all(value["before"] is None for value in addition[0]["fields"].values())
    before = db.get(ctx.project_id)
    submit(ctx, {"remove_contract_keys": ["extra"]})
    removal = assert_bound_audit(db, before, db.project)["contract_changes"]
    assert len(removal) == 1 and removal[0]["operation"] == "removed"
    assert removal[0]["before_revision_id"] == addition[0]["after_revision_id"]
    assert removal[0]["after_revision_id"] is None
    assert all(value["after"] is None for value in removal[0]["fields"].values())
    assert db.project.behaviors == before.behaviors


@pytest.mark.parametrize("field", ["component_ids", "requires_behavior_keys"])
def test_explicit_empty_optional_links_clear_only_named_field(app, field):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    before = db.get(ctx.project_id)
    submit(ctx, {"contracts": [{"key": "cli", field: []}]})
    changes = assert_bound_audit(db, before, db.project)["contract_changes"]
    assert len(changes) == 1 and changes[0]["key"] == "cli"
    assert set(changes[0]["fields"]) == {field}
    assert changes[0]["fields"][field]["after"] == []
    assert active_behaviors(db.project)["core"] == active_behaviors(before)["core"]


@pytest.mark.parametrize("changes", [
    {"statement": None}, {"mechanism": None}, {"owner": None}, {"requirement_ids": None},
    {"acceptance_scope": None}, {"component_ids": None}, {"requires_behavior_keys": None},
    {"statement": ""}, {"statement": "  "}, {"mechanism": ""}, {"mechanism": "  "},
    {"requirement_ids": []}, {"owner": ""}, {"owner_change_reason": " "},
    {"statement": 4}, {"requirement_ids": [1]}, {"component_ids": [True]},
    {"requires_behavior_keys": "core"}, {"acceptance_scope": "unknown"},
    {"changed_fields": ["statement"]}, {"behavior_revision_id": "invented"},
])
def test_invalid_field_values_are_atomic(app, changes):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    unchanged_on_error(ctx, db, {"contracts": [{"key": "cli", **changes}]}, ".")


@pytest.mark.parametrize("field", ["owner", "statement", "requirement_ids", "mechanism"])
def test_new_contract_requires_all_core_fields(app, field):
    ctx, db = stage(app)
    initial(ctx)
    value = contract("new")
    del value[field]
    unchanged_on_error(ctx, db, {"contracts": [value]}, "new or incomplete contract requires.*" + field)


@pytest.mark.parametrize("field,value", [
    ("owner", "MISSING"), ("requirement_ids", ["missing"]),
    ("component_ids", ["missing"]), ("requires_behavior_keys", ["missing"]),
])
def test_unknown_field_references_are_atomic(app, field, value):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    unchanged_on_error(ctx, db, {"contracts": [{"key": "cli", field: value}]}, "unknown reference")


def test_owner_only_move_requires_reason_and_preserves_other_fields_and_history(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("A", behaviors=[behavior("a"), behavior("move", acceptance_scope="milestone")]), node("B")])
    before = db.get(ctx.project_id)
    unchanged_on_error(ctx, db, {"contracts": [{"key": "move", "owner": "B"}]}, "owner_change_reason")
    unchanged_on_error(ctx, db, {"contracts": [{"key": "move", "owner_change_reason": "No move"}]}, "active owner change")
    payload = {"contracts": [{"key": "move", "owner": "B", "owner_change_reason": "B now owns this acceptance"}]}
    result = submit(ctx, payload)
    after = db.get(ctx.project_id)
    current, previous = active_behaviors(after)["move"], active_behaviors(before)["move"]
    assert current.behavior_key == previous.behavior_key
    assert current.owner == "B" and current.statement == previous.statement
    assert current.acceptance_scope == "milestone" and current.supersedes == previous.id
    assert after.behaviors[:len(before.behaviors)] == before.behaviors
    assert result["restored_inactive_behaviors"] == []
    changes = assert_bound_audit(db, before, after)["contract_changes"]
    assert len(changes) == 1 and changes[0]["fields"] == {"owner": {"before": "A", "after": "B"}}
    assert changes[0]["owner_change_reason"] == payload["contracts"][0]["owner_change_reason"]
    assert changes[0]["before_owner"] == "A" and changes[0]["after_owner"] == "B"
    assert history_findings(before, after) == []


def test_owner_swap_with_explicit_reasons_has_one_final_owner_per_stable_key(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("A"), node("B")])
    before = db.get(ctx.project_id)
    submit(ctx, {"contracts": [
        {"key": "a", "owner": "B", "owner_change_reason": "B takes A responsibility"},
        {"key": "b", "owner": "A", "owner_change_reason": "A takes B responsibility"},
    ]})
    assert active_behaviors(db.project)["a"].owner == "B"
    assert active_behaviors(db.project)["b"].owner == "A"
    assert db.project.behaviors[:len(before.behaviors)] == before.behaviors
    assert len(assert_bound_audit(db, before, db.project)["contract_changes"]) == 2


@pytest.mark.parametrize("change", [{}, {"statement": "A real revised statement"}, {"mechanism": "A real revised mechanism"}])
def test_active_older_revision_preserved_or_append_after_latest_history(app, change):
    ctx, db = stage(app)
    initial(ctx, milestones=[node(behaviors=[behavior("check"), behavior("unchanged")])])
    project = db.get(ctx.project_id)
    old = active_behaviors(project)
    newer = {}
    for key in old:
        newer[key] = BehaviorRevision(
            behavior_key=key, statement="Unused newer historical variant", owner="CHECK",
            version=old[key].version + 1, supersedes=old[key].id, acceptance_scope="milestone",
        )
        project.behaviors.append(newer[key])
    ctx, db = stage(app, project)
    before = db.get(ctx.project_id)
    submit(ctx, {"contracts": [{"key": "check", **change}]})
    current = active_behaviors(db.project)
    assert current["unchanged"] == old["unchanged"]
    assert db.project.behaviors[:len(before.behaviors)] == before.behaviors
    if not change:
        assert db.project == before
    else:
        assert current["check"].supersedes == newer["check"].id
        assert current["check"].version == newer["check"].version + 1
        assert current["check"].acceptance_scope == old["check"].acceptance_scope
    changes = assert_bound_audit(db, before, db.project)["contract_changes"]
    assert [item["key"] for item in changes] == (["check"] if change else [])
    assert history_findings(before, db.project) == []


@pytest.mark.parametrize("tamper", ["base_revision", "base_candidate_hash", "project_id", "compiled_hash", "patch"])
def test_mismatched_compiler_envelope_rejected_without_checkpoint(app, tamper):
    ctx, db = stage(app)
    initial(ctx)
    compiled = compile_plan_delta(db.project, {"contracts": [{"key": "check", "mechanism": "Changed mechanism"}]})
    audit, patch = deepcopy(compiled.audit), compiled.patch.model_copy(deep=True)
    if tamper == "patch":
        patch.summary = "Tampered compiled patch"
    else:
        audit[tamper] = -1 if tamper == "base_revision" else "not-the-current-value"
    before, record, events = db.project.model_dump(), deepcopy(db.record), deepcopy(db.saved_events)
    with pytest.raises(ValueError, match="exact candidate revision/hash"):
        propose_plan_patch(ctx, patch, compiler_audit=audit)
    assert_unchanged(db, before, record, events)


def test_diff_is_server_computed_and_historical_v1_audit_is_not_rewritten(app):
    ctx, db = stage(app)
    initial(ctx)
    compiled = compile_plan_delta(db.project, {"contracts": [{"key": "check", "mechanism": "Changed mechanism"}]})
    before = db.get(ctx.project_id)
    audit = {**compiled.audit, "contract_changes": [{"key": "invented"}], "audit_hash": "invented"}
    propose_plan_patch(ctx, compiled.patch, compiler_audit=audit)
    saved = assert_bound_audit(db, before, db.project)
    assert saved["contract_changes"] == contract_changes(before, db.project)
    assert saved["contract_changes"][0]["key"] == "check"
    historical = {"protocol_version": "plan-delta/v1", "ir": {}, "compiled_hash": "historical"}
    before = db.project.model_dump()
    empty = compile_plan_delta(db.project, {})
    propose_plan_patch(ctx, empty.patch, compiler_audit=historical)
    assert db.project.model_dump() == before
    assert db.record["compilations"][-1] == historical
    assert db.record["compilations"][-2] == saved


def test_contract_schema_advertises_key_only_updates_without_null():
    schema = DELTA_TOOL.schema()["function"]["parameters"]["$defs"]["ContractDelta"]
    assert schema["required"] == ["key"]
    assert "owner_change_reason" in schema["properties"]
    assert PlanDelta.model_validate({"contracts": [{"key": "existing"}]}).contracts[0].model_fields_set == {"key"}
