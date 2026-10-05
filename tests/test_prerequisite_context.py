"""Exact declared prerequisite mechanisms, with bounded selection and deduplication."""
from copy import deepcopy

import pytest
from evograph.application.plan_units import _prerequisite_context as projection
from evograph.application.plan_units import prepare_unit_request
from test_architecture_unit_bootstrap import manifest_for, schedule
from test_plan_patch import behavior, initial, node, stage


def fixture():
    contract = {"key": "provider", "owner": "dep", "revision_id": "r-current",
                "statement": "Existing provider interface", "acceptance_scope": "target",
                "mechanism": "Exact saved provider mechanism", "requires_behavior_keys": ["transitive"]}
    objects = {"contract:provider": contract}
    context = {
        "slices": [{"id": "dep", "dependencies": ["transitive-slice"]},
                   {"id": "transitive-slice", "dependencies": []}],
        "acceptance_directory": [{k: deepcopy(v) for k, v in contract.items() if k != "mechanism"}],
        "completed_contract_mechanisms": [],
        "current_unit": {"changes": [
            {"kind": "slice", "id": "consumer", "uses": [
                {"field": "dependencies", "kind": "slice", "id": "dep"}]}], "objects": {}},
    }
    return context, objects


def test_new_declared_slice_not_saved_is_explicit_and_never_uses_future_provider():
    context, objects = fixture()
    context["slices"] = []
    result = projection(context, objects)
    assert result["contracts"] == []
    assert result["missing"] == [{"kind": "slice", "id": "dep", "reason": "not_saved_in_this_snapshot"}]


def test_direct_contract_reference_is_selected_without_owner_slice_expansion():
    context, objects = fixture()
    context["current_unit"]["changes"] = [{"kind": "contract", "id": "consumer", "uses": [
        {"field": "requires_behavior_keys", "kind": "contract", "id": "provider"}]}]
    result = projection(context, objects)
    assert result["slice_ids"] == []
    assert result["direct_contract_keys"] == ["provider"]
    assert [item["key"] for item in result["contracts"]] == ["provider"]
    assert result["missing"] == []


def test_multiple_declared_paths_select_each_provider_once():
    context, objects = fixture()
    context["current_unit"]["changes"].append(deepcopy(context["current_unit"]["changes"][0]))
    context["current_unit"]["changes"][-1]["id"] = "second-consumer"
    context["current_unit"]["changes"].append({"kind": "contract", "id": "consumer", "uses": [
        {"field": "requires_behavior_keys", "kind": "contract", "id": "provider"}]})
    result = projection(context, objects)
    assert result["slice_ids"] == ["dep"]
    assert len(result["contracts"]) == 1


@pytest.mark.parametrize("location", ["full", "completed"])
def test_exact_existing_mechanism_is_referenced_without_repeating_text(location):
    context, objects = fixture()
    contract = deepcopy(objects["contract:provider"])
    if location == "full":
        context["current_unit"]["objects"]["contract:provider"] = contract
    else:
        context["completed_contract_mechanisms"] = [contract]
    result = projection(context, objects)
    row = result["contracts"][0]
    assert "mechanism" not in row
    assert "mechanism_ref" in row
    assert row["revision_id"] == "r-current"


def test_wrong_source_revision_is_reported_and_not_projected():
    context, objects = fixture()
    objects["contract:provider"]["revision_id"] = "future-or-stale"
    result = projection(context, objects)
    assert result["contracts"] == []
    assert result["missing"][0]["reason"] == "revision_mismatch"


@pytest.mark.parametrize("mutation", ["revision_id", "mechanism"])
def test_stale_or_different_existing_mechanism_does_not_suppress_exact_source(mutation):
    context, objects = fixture()
    old = deepcopy(objects["contract:provider"])
    old[mutation] = "stale-or-different"
    context["current_unit"]["objects"]["contract:provider"] = deepcopy(old)
    context["completed_contract_mechanisms"] = [deepcopy(old)]
    row = projection(context, objects)["contracts"][0]
    assert row["mechanism"] == objects["contract:provider"]["mechanism"]
    assert "mechanism_ref" not in row


def test_dependencies_and_contract_requires_are_not_followed_transitively():
    context, objects = fixture()
    transitive = {**deepcopy(objects["contract:provider"]), "key": "transitive", "owner": "transitive-slice"}
    objects["contract:transitive"] = transitive
    context["acceptance_directory"].append({k: v for k, v in transitive.items() if k != "mechanism"})
    result = projection(context, objects)
    assert [item["key"] for item in result["contracts"]] == ["provider"]


def test_missing_active_contract_key_is_reported():
    context, objects = fixture()
    context["current_unit"]["changes"] = [{"kind": "contract", "id": "consumer", "uses": [
        {"field": "requires_behavior_keys", "kind": "contract", "id": "missing"}]}]
    assert projection(context, objects)["missing"] == [
        {"kind": "contract", "id": "missing", "reason": "active_definition_unavailable"}]


def test_architecture_membership_and_owner_references_do_not_select_providers():
    context, objects = fixture()
    context["current_unit"]["changes"] = [
        {"kind": "architecture", "id": "architecture", "uses": [
            {"field": "architecture_milestone_ids", "kind": "slice", "id": "dep"}]},
        {"kind": "contract", "id": "consumer", "uses": [
            {"field": "owner", "kind": "slice", "id": "dep"}]},
    ]
    assert projection(context, objects) is None


def test_projection_is_read_only_and_context_mutation_does_not_change_source():
    context, objects = fixture()
    old_context, old_objects = deepcopy(context), deepcopy(objects)
    result = projection(context, objects)
    result["contracts"][0]["mechanism"] = "Consumer mutation"
    result["slice_ids"].clear()
    assert context == old_context and objects == old_objects


def test_saved_source_slice_is_not_mislabeled_as_unsaved():
    context, objects = fixture()
    context["slices"] = []
    context["source_slices"] = [{"id": "dep"}]
    context["acceptance_directory"] = []
    result = projection(context, objects)
    assert result["contracts"] == result["missing"] == []
    assert result["slice_ids"] == ["dep"]


@pytest.mark.parametrize("direct_contract", [False, True])
def test_generation_context_includes_only_the_declared_current_provider(app, direct_contract):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("BASE", behaviors=[behavior("base")]),
                             node("UNRELATED", behaviors=[behavior("unrelated")])])
    delta = {
        "slices": [{"id": "CONSUMER", "title": "Consumer", "intent": "Use the existing parser",
                    "scope": ["consumer.py"], "dependencies": ["BASE"],
                    "dependency_reasons": {"BASE": "Consumes the existing parser result"}}],
        "contracts": [{"key": "consumer", "owner": "CONSUMER", "statement": "Report a parser result",
                       "requirement_ids": ["r"], "mechanism": "Read the existing parser result",
                       **({"requires_behavior_keys": ["base"]} if direct_contract else {})}],
    }
    schedule(ctx, manifest_for(delta))
    context = prepare_unit_request(db)
    details = context["prerequisite_context"]
    assert details["slice_ids"] == ["BASE"]
    assert details["direct_contract_keys"] == (["base"] if direct_contract else [])
    assert [item["key"] for item in details["contracts"]] == ["base"]
    row = details["contracts"][0]
    entry = next(item for item in context["acceptance_directory"] if item["key"] == "base")
    assert row["revision_id"] == entry["revision_id"]
    assert details["missing"] == []
    if direct_contract:
        assert row["mechanism_ref"] == 'current_unit.objects["contract:base"].mechanism'
        assert "mechanism" not in row
    else:
        assert row["mechanism"] == next(binding.mechanism for binding in db.project.plan_contract.bindings
                                       if binding.behavior_key == "base")
