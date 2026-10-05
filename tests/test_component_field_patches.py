"""Bounded offline replay and atomic checks for component field updates."""

import hashlib
import json
from pathlib import Path

import pytest
from evograph.application.plan_ir import DELTA_TOOL, PlanDelta, compile_plan_delta
from evograph.application.plan_units import manifest_field_catalog
from evograph.domain.models import Project
from test_plan_ir import submit, unchanged_on_error
from test_plan_patch import architecture, initial, stage


def test_captured_qa55_raw_unit_preserves_existing_components():
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "qa55_component_update_unit.json").read_text())
    raw = fixture["raw_arguments"]
    assert hashlib.sha256(raw.encode()).hexdigest() == fixture["provenance"]["raw_arguments_sha256"]
    payload = json.loads(raw)
    project = Project.model_validate(fixture["base_project"])
    before = project.model_dump()
    old = project.architectures[-1]
    old_nodes = {node.id: node.model_dump() for node in old.diagram.nodes}
    updates = [item for item in payload["components"] if item["id"] in old_nodes]
    additions = [item for item in payload["components"] if item["id"] not in old_nodes]
    assert len(updates) == 4 and len(additions) == 2
    assert all(set(item) == {"id", "description"} for item in updates)

    # Exercise both the dict and already-validated model compiler paths.
    compiled = compile_plan_delta(project, payload)
    assert compile_plan_delta(project, PlanDelta.model_validate_json(raw)).audit == compiled.audit
    assert compiled.audit["ir"] == payload
    assert project.model_dump() == before
    expected = old.model_dump(exclude={"number", "created_at"})
    changes = {item["id"]: item for item in updates}
    expected["diagram"]["nodes"] = [
        {**node, **changes.get(node["id"], {})} for node in expected["diagram"]["nodes"]
    ] + [{**item, "source_refs": [], "source_ref_count": 0} for item in additions]
    assert compiled.patch.architecture.model_dump() == expected
    assert old_nodes["comp-customer-web"]["role"] == "frontend"
    assert old_nodes["comp-booking-store"]["role"] == "database"
    assert compiled.patch.milestones[0].behaviors[0].statement == payload["contracts"][0]["statement"]


@pytest.mark.parametrize("changes", [
    {"label": "New label"}, {"description": "New responsibility"},
    {"role": "security"}, {"source_refs": []},
])
def test_one_field_component_update_preserves_rich_diagram(app, repository, changes):
    project = app.projects.create("Component patch", repository=str(repository))
    ctx, db = stage(app, project)
    spec = architecture("web", "store", decisions=["Keep exact decisions"], risks=["Keep risk"])
    spec["diagram"].update({"title": "Saved title", "milestone_ids": ["CHECK"],
        "edges": [{"source": "web", "target": "store", "label": "reads"}],
        "groups": [{"id": "system", "label": "Saved group", "kind": "domain",
                    "member_node_ids": ["web", "store"]}]})
    spec["diagram"]["nodes"][0].update(role="frontend", source_refs=["auth.py"], source_ref_count=3)
    spec["diagram"]["nodes"][1].update(role="database")
    initial(ctx, architecture=spec)
    before = db.project.model_copy(deep=True)
    payload = {"components": [{"id": "web", **changes}]}
    args = PlanDelta.model_validate(payload)
    assert args.components[0].model_fields_set == {"id", *changes}
    submit(ctx, payload)
    expected = before.architectures[-1].model_dump(exclude={"number", "created_at"})
    expected["diagram"]["nodes"][0].update(changes)
    if "source_refs" in changes:
        expected["diagram"]["nodes"][0]["source_ref_count"] = len(changes["source_refs"])
    assert db.project.architectures[-1].model_dump(exclude={"number", "created_at"}) == expected
    assert db.project.architectures[:-1] == before.architectures
    assert db.project.behaviors == before.behaviors
    assert db.project.plan_contract == before.plan_contract


def test_id_only_component_update_is_noop(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core"))
    before = db.project.model_dump()
    result = submit(ctx, {"components": [{"id": "core"}]})
    assert result["status"] == "NO_PROGRESS"
    assert db.project.model_dump() == before


@pytest.mark.parametrize("missing", ["label", "description"])
def test_incomplete_new_component_rejects_whole_mixed_delta(app, missing):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core"))
    new = {"id": "new", "label": "New component", "description": "New responsibility"}
    del new[missing]
    unchanged_on_error(ctx, db, {
        "slices": [{"id": "CHECK", "title": "Must not save"}],
        "components": [{"id": "core", "description": "Must not save"}, new],
    }, f"components.new: new component requires {missing}")


@pytest.mark.parametrize("changes", [
    {"label": None}, {"description": None}, {"role": None}, {"source_refs": None},
    {"label": ""}, {"description": ""}, {"label": 12}, {"description": []},
    {"role": "unknown"}, {"source_refs": [12]}, {"unknown_field": "value"},
])
def test_explicit_invalid_component_fields_still_reject_atomically(app, changes):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core"))
    unchanged_on_error(ctx, db, {
        "slices": [{"id": "CHECK", "title": "Must not save"}],
        "components": [{"id": "core", **changes}],
    }, "validation error")


def test_component_schema_and_manifest_share_presence_rules(app):
    schema = DELTA_TOOL.schema()["function"]["parameters"]["$defs"]["ComponentDelta"]
    assert schema["required"] == ["id"]
    for field in ("label", "description"):
        assert schema["properties"][field]["type"] == "string"
        assert "default" not in schema["properties"][field]
    catalog = manifest_field_catalog()["component"]
    assert catalog["required_existing"] == []
    assert catalog["required_new"] == ["description", "label"]
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core"))
    submit(ctx, {"components": [{"id": "new", "label": "New", "description": "New work"}]})
    assert db.project.architectures[-1].diagram.nodes[-1].role == "backend"
