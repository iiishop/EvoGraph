"""Offline evidence for the shallow protocol and its existing atomic compiler."""

import hashlib
import json
from copy import deepcopy

import pytest
from evograph.application.plan_budget import compact_schema, encoded_size
from evograph.application.plan_ir import (
    DELTA_TOOL,
    PROTOCOL_VERSION,
    PlanDelta,
    compile_plan_delta,
    submit_plan_delta,
)
from evograph.domain.models import Attachment, ResearchSource
from evograph.domain.plan_contracts import active_behaviors, contract_findings
from evograph.infrastructure.database import ConflictError
from pydantic import ValidationError
from test_plan_patch import INPUT, architecture, assert_unchanged, behavior, initial, node, stage


def contract(key="check", owner="CHECK", **changes):
    return {"key": key, "owner": owner, "statement": f"Observable {key} result",
            "requirement_ids": ["r"], "mechanism": f"Resolve {key} through the local parser", **changes}


def slice_(mid="CHECK", **changes):
    return {"id": mid, "title": mid, "intent": f"Deliver {mid}", "scope": [f"{mid.lower()}.py"],
            **changes}


def component(cid, **changes):
    return {"id": cid, "label": cid, "description": f"Owns {cid} state and operations", **changes}


def complete_delta():
    return {
        "requirements": [{"id": "r", "quote": INPUT, "kind": "outcome"}], "target": INPUT,
        "slices": [slice_("CLI", dependencies=["CORE"], dependency_reasons={"CORE": "Needs results"}),
                   slice_("CORE")],
        "contracts": [contract("cli", "CLI", component_ids=["cli"], requires_behavior_keys=["core"]),
                      contract("core", "CORE", component_ids=["parser"])],
        "components": [component("parser"), component("cli")],
        "relations": [{"source": "cli", "target": "parser", "label": "calls"}],
        "architecture_summary": "Local parser and CLI",
        "technologies": [{"area": "runtime", "choice": "Python", "rationale": "Local execution"}],
    }


def submit(ctx, payload):
    return submit_plan_delta(ctx, PlanDelta.model_validate(payload))


def unchanged_on_error(ctx, db, payload, match):
    before, record, events = db.project.model_dump(), deepcopy(db.record), deepcopy(db.saved_events)
    with pytest.raises(ValueError, match=match):
        submit(ctx, payload)
    assert_unchanged(db, before, record, events)


def test_complete_two_slice_delta_and_audit_share_one_candidate_checkpoint(app, monkeypatch):
    ctx, db = stage(app)
    payload = complete_delta()
    original = deepcopy(payload)
    before = db.project.model_dump()
    canonical = app.db.get(ctx.project_id).model_dump()
    compiled = compile_plan_delta(db.project, payload)
    assert db.project.model_dump() == before and payload == original
    writes = []
    save = db.store.save
    monkeypatch.setattr(db.store, "save", lambda record: (writes.append(deepcopy(record)), save(record))[1])
    result = submit(ctx, payload)
    assert result["findings"] == contract_findings(db.project) == []
    assert len(writes) == len(db.saved_events) == len(db.project.targets) == len(db.project.plans) == 1
    assert db.project.milestone("CLI").dependencies == ["CORE"]
    assert db.project.milestone("CORE").architecture_components == ["parser"]
    assert app.db.get(ctx.project_id).model_dump() == canonical
    assert len(db.record["compilations"]) == 1
    assert {key: db.record["compilations"][0][key] for key in compiled.audit} == compiled.audit
    assert compiled.audit["ir"] == payload
    assert compiled.audit["protocol_version"] == PROTOCOL_VERSION
    encoded = json.dumps(compiled.patch.model_dump(mode="json", exclude_unset=True),
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert compiled.audit["compiled_hash"] == hashlib.sha256(encoded).hexdigest()
    assert compiled.audit["compiled_hash"] != db.record["candidate_hash"]
    assert db.project.architectures[-1].quality_scenarios == []
    assert db.project.architectures[-1].risks == []


def test_one_contract_edit_reconstructs_owner_and_preserves_unmentioned_acceptance(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node(behaviors=[behavior("check"), behavior("unchanged")]), node("OTHER")])
    before = db.get(ctx.project_id)
    payload = {"contracts": [contract(statement="Refined single observable result")]}
    compiled = compile_plan_delta(before, payload)
    assert [m.id for m in compiled.patch.milestones] == ["CHECK"]
    assert [b.key for b in compiled.patch.milestones[0].behaviors] == ["check", "unchanged"]
    assert compiled.audit["ir"] == payload
    submit(ctx, payload)
    after = db.get(ctx.project_id)
    assert active_behaviors(after)["check"].id != active_behaviors(before)["check"].id
    assert active_behaviors(after)["unchanged"] == active_behaviors(before)["unchanged"]
    assert after.milestone("OTHER") == before.milestone("OTHER")
    assert after.behaviors[:len(before.behaviors)] == before.behaviors
    assert after.plan_contract.requirements == before.plan_contract.requirements
    assert after.plan_contract.sources == before.plan_contract.sources
    assert after.architectures == before.architectures


def test_omitted_contract_scope_and_links_and_slice_metadata_are_retained(app):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    # Establish local-only acceptance plus ancillary service metadata.
    p = db.get(ctx.project_id)
    cli = active_behaviors(p)["cli"]
    cli.acceptance_scope = "milestone"
    p.milestone("CLI").resources = ["terminal"]
    p.milestone("CLI").change_types = ["api"]
    ctx, db = stage(app, p)
    before = db.get(ctx.project_id)
    submit(ctx, {"contracts": [contract("cli", "CLI", statement="Changed local result")]})
    after = db.get(ctx.project_id)
    old, new = before.milestone("CLI"), after.milestone("CLI")
    for field in ("dependencies", "dependency_reasons", "resources", "change_types", "scope",
                  "title", "intent", "architecture_components"):
        assert getattr(new, field) == getattr(old, field)
    assert active_behaviors(after)["cli"].acceptance_scope == "milestone"
    binding = next(b for b in after.plan_contract.bindings if b.behavior_key == "cli")
    assert binding.component_ids == ["cli"] and binding.requires_behavior_keys == ["core"]
    assert after.architectures == before.architectures


def test_slice_title_only_delta_does_not_resend_or_change_contracts(app):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    before = db.get(ctx.project_id)
    submit(ctx, {"slices": [{"id": "CLI", "title": "Precise terminal interface"}]})
    assert db.project.behaviors == before.behaviors
    assert db.project.plan_contract == before.plan_contract
    assert db.project.architectures == before.architectures
    assert db.project.milestone("CORE") == before.milestone("CORE")
    assert db.project.milestone("CLI").title == "Precise terminal interface"


def test_existing_rich_architecture_and_source_metadata_are_exactly_retained(app, repository):
    p = app.projects.create("Rich architecture", repository=str(repository))
    p.research = [ResearchSource(id="reference", title="Local reference", url="https://example.test/",
                                 excerpt="Design context", query="fixture")]
    p.attachments = [Attachment(id="a", name="Notes", media_type="text/plain", size=0)]
    ctx, db = stage(app, p)
    spec = architecture("core", "cli", decisions=["Keep parsing local"], risks=["Invalid paths"],
                        quality_scenarios=[{"concern": "locality", "scenario": "No network",
                                            "measure": "Zero requests", "approach": "Local resolver"}],
                        research_ids=["reference"])
    spec["diagram"].update({"id": "saved-diagram", "title": "Saved title",
                           "groups": [{"id": "app", "label": "Application", "kind": "domain",
                                       "member_node_ids": ["core", "cli"]}],
                           "milestone_ids": ["CHECK"], "attachment_ids": ["a"]})
    spec["diagram"]["nodes"][0].update(role="security", source_refs=["auth.py"], source_ref_count=3)
    initial(ctx, architecture=spec, milestones=[node(behaviors=[behavior("check", component_ids=["core"])])])
    before = db.get(ctx.project_id)
    submit(ctx, {"components": [component("core", label="Refined name", description="Refined ownership")]})
    after = db.get(ctx.project_id)
    expected = before.architectures[-1].model_dump(exclude={"number", "created_at"})
    expected["diagram"]["nodes"][0].update(label="Refined name", description="Refined ownership")
    assert after.architectures[-1].model_dump(exclude={"number", "created_at"}) == expected
    assert after.architectures[:-1] == before.architectures


def test_component_migration_uses_existing_design_owner_rule(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("old"), milestones=[node(behaviors=[behavior("check", component_ids=["old"])])])
    before = db.get(ctx.project_id)
    submit(ctx, {
        "components": [component("new")], "remove_component_ids": ["old"],
        "retirements": [{"component_id": "old", "milestone_id": "MIGRATE", "instruction": "Replace old adapter"}],
        "slices": [slice_("MIGRATE")],
        "contracts": [contract(component_ids=["new"]), contract("migration", "MIGRATE", component_ids=["new"])],
    })
    after = db.get(ctx.project_id)
    assert [n.id for n in after.architectures[-1].diagram.nodes] == ["new"]
    assert after.architectures[:-1] == before.architectures
    step = after.milestone("MIGRATE").migration_steps[0]
    assert step.component_id == "old" and step.from_revision == 1
    assert after.milestone("CHECK").architecture_components == ["new"]


def test_completed_migration_owner_is_rejected_atomically(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("old"), milestones=[node(behaviors=[behavior("check", component_ids=["old"])]), node("MIGRATE")])
    p = db.get(ctx.project_id)
    p.milestone("MIGRATE").status = "VERIFIED_COMPLETE"
    ctx, db = stage(app, p)
    unchanged_on_error(ctx, db, {
        "components": [component("new")], "remove_component_ids": ["old"],
        "retirements": [{"component_id": "old", "milestone_id": "MIGRATE", "instruction": "Replace adapter"}],
        "contracts": [contract(component_ids=["new"])],
    }, "尚未完成")


def test_explicit_removals_repair_relations_dependencies_and_contract_links(app):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    before = db.get(ctx.project_id)
    submit(ctx, {
        "remove_slice_ids": ["CORE"], "remove_contract_keys": ["core"], "remove_component_ids": ["parser"],
        "remove_relations": [{"source": "cli", "target": "parser", "label": "calls"}],
        "retirements": [{"component_id": "parser", "milestone_id": "CLI", "instruction": "Inline parser into CLI"}],
        "slices": [{"id": "CLI", "dependencies": []}],
        "contracts": [contract("cli", "CLI", requires_behavior_keys=[])],
    })
    assert [m.id for m in db.project.milestones] == ["CLI"]
    assert db.project.milestone("CLI").dependencies == []
    assert db.project.milestone("CLI").dependency_reasons == {}
    assert db.project.architectures[-1].diagram.edges == []
    assert db.project.behaviors[:len(before.behaviors)] == before.behaviors
    assert [b.behavior_key for b in db.project.plan_contract.bindings] == ["cli"]


def test_remove_one_contract_retains_others_and_requires_explicit_empty_slice_removal(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node(behaviors=[behavior("check"), behavior("spare")])])
    submit(ctx, {"remove_contract_keys": ["spare"]})
    assert set(active_behaviors(db.project)) == {"check"}
    unchanged_on_error(ctx, db, {"remove_contract_keys": ["check"]}, "empty slice")


def test_contract_moves_reconstruct_both_owners_without_ambiguity(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("A", behaviors=[behavior("a"), behavior("move")]), node("B")])
    submit(ctx, {"contracts": [contract("move", "B", owner_change_reason="B now owns this acceptance")]})
    assert {b.behavior_key for b in active_behaviors(db.project).values() if b.owner == "A"} == {"a"}
    assert {b.behavior_key for b in active_behaviors(db.project).values() if b.owner == "B"} == {"b", "move"}


@pytest.mark.parametrize("payload, location", [
    ({"repository": "/tmp"}, ("repository",)),
    ({"slices": [{"id": "S", "behaviors": []}]}, ("slices", 0, "behaviors")),
    ({"contracts": [{**contract(), "components": []}]}, ("contracts", 0, "components")),
    ({"requirements": [{"id": "r", "quote": "x", "kind": "outcome", "scope": "process"}]}, ("requirements", 0, "scope")),
    ({"process_constraints": [{"id": "p", "quote": "x", "rule": "unknown"}]}, ("process_constraints", 0, "rule")),
    ({"contracts": [{**contract(), "owner": 1}]}, ("contracts", 0, "owner")),
    ({"contracts": [{**contract(), "statement": True}]}, ("contracts", 0, "statement")),
    ({"contracts": [{**contract(), "requirement_ids": "r"}]}, ("contracts", 0, "requirement_ids")),
    ({"slices": [{"id": "S", "dependencies": [1]}]}, ("slices", 0, "dependencies", 0)),
    ({"slices": [{"id": "S", "dependency_reasons": {"A": False}}]}, ("slices", 0, "dependency_reasons", "A")),
    ({"requirements": [{"id": "r", "quote": 123, "kind": "outcome"}]}, ("requirements", 0, "quote")),
    ({"components": [{**component("c"), "source_ref_count": 10}]}, ("components", 0, "source_ref_count")),
    ({"retirements": [{"component_id": "c", "milestone_id": "S", "instruction": "Remove", "from_revision": "1"}]}, ("retirements", 0, "from_revision")),
    ({"technologies": [{"area": "runtime", "choice": 3, "rationale": "local"}]}, ("technologies", 0, "choice")),
    ({"slices": (slice_(),)}, ("slices",)),
    ({"target": None}, ("target",)),
    ({"architecture_summary": None}, ("architecture_summary",)),
    ({"decisions": None}, ("decisions",)),
    ({"technologies": None}, ("technologies",)),
    ({"contracts": None}, ("contracts",)),
    ({"slices": [{"id": "S", "title": None}]}, ("slices", 0, "title")),
    ({"requirements": [{"id": "r", "quote": "x", "kind": "outcome", "source_id": None}]}, ("requirements", 0, "source_id")),
    ({"process_constraints": [{"id": "p", "quote": "x", "rule": "other", "source_id": None}]}, ("process_constraints", 0, "source_id")),
    ({"architecture_groups": None}, ("architecture_groups",)),
    ({"architecture_milestone_ids": None}, ("architecture_milestone_ids",)),
    ({"risks": None}, ("risks",)),
    ({"quality_scenarios": None}, ("quality_scenarios",)),
    ({"architecture_groups": [{"id": "g", "label": "Group", "member_node_ids": [1]}]}, ("architecture_groups", 0, "member_node_ids", 0)),
    ({"architecture_milestone_ids": [1]}, ("architecture_milestone_ids", 0)),
    ({"risks": [True]}, ("risks", 0)),
    ({"quality_scenarios": [{"concern": "Latency", "scenario": "Request", "measure": 10, "approach": "Local"}]}, ("quality_scenarios", 0, "measure")),
    ({"architecture_groups": [{"id": "g", "label": "Group", "member_node_ids": ["c"], "nodes": []}]}, ("architecture_groups", 0, "nodes")),
    ({"quality_scenarios": [{"concern": "Latency", "scenario": "Request", "measure": "10 ms", "approach": "Local", "verified": True}]}, ("quality_scenarios", 0, "verified")),
    ({"components": [{**component("c"), "source_refs": None}]}, ("components", 0, "source_refs")),
    ({"components": [{**component("c"), "source_refs": [3]}]}, ("components", 0, "source_refs", 0)),
])
def test_strict_schema_rejects_exact_unknown_types_and_misnesting(payload, location):
    with pytest.raises(ValidationError) as caught:
        PlanDelta.model_validate(payload)
    assert location in [error["loc"] for error in caught.value.errors()]


@pytest.mark.parametrize("payload, match", [
    ({"contracts": [contract("cli", "missing")]}, "contracts.owner"),
    ({"contracts": [contract("cli", "CLI", requirement_ids=["missing"])]}, "requirement_ids"),
    ({"contracts": [contract("cli", "CLI", component_ids=["missing"])]}, "component_ids"),
    ({"contracts": [contract("cli", "CLI", requires_behavior_keys=["missing"])]}, "requires_behavior_keys"),
    ({"slices": [{"id": "CLI", "dependencies": ["missing"]}]}, "dependencies"),
    ({"slices": [{"id": "CLI", "dependencies": []}], "remove_slice_ids": ["CORE"]}, "requires_behavior_keys"),
    ({"remove_slice_ids": ["CORE"]}, "requires_behavior_keys|dependencies"),
    ({"contracts": [contract("cli", "CLI"), contract("cli", "CORE")]}, "contracts.key"),
    ({"slices": [{"id": "CLI"}, {"id": "CLI"}]}, "slices.id"),
    ({"components": [component("cli"), component("cli")]}, "components.id"),
    ({"relations": [{"source": "cli", "target": "missing", "label": "calls"}]}, "relations"),
    ({"remove_relations": [{"source": "cli", "target": "parser", "label": "wrong"}]}, "remove_relations"),
    ({"remove_component_ids": ["parser"]}, "relations|retirements"),
    ({"remove_contract_keys": ["missing"]}, "remove_contract_keys"),
    ({"remove_slice_ids": ["missing"]}, "remove_slice_ids"),
    ({"remove_component_ids": ["missing"]}, "remove_component_ids"),
    ({"slices": [{"id": "CLI", "scope": None}]}, "omit unchanged"),
    ({"slices": [{"id": "NEW"}], "contracts": [contract("new", "NEW")]}, "new slice requires"),
    ({"components": [component("cli")], "remove_component_ids": ["cli"]}, "upsert and remove"),
    ({"contracts": [contract("cli", "CLI")], "remove_contract_keys": ["cli"]}, "upsert and remove"),
    ({"slices": [{"id": "CLI"}], "remove_slice_ids": ["CLI"]}, "upsert and remove"),
    ({"restore_contract_keys": ["missing"]}, "restore_contract_keys"),
    ({"contracts": [contract("cli", "CLI", requirement_ids=["r", "r"])]}, "duplicate"),
])
def test_invalid_delta_is_rejected_without_any_candidate_or_audit_mutation(app, payload, match):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    unchanged_on_error(ctx, db, payload, match)


def test_preserved_group_reference_is_not_silently_pruned(app):
    ctx, db = stage(app)
    spec = architecture("old", "keep")
    spec["diagram"]["groups"] = [{"id": "group", "label": "Saved grouping", "member_node_ids": ["old", "keep"]}]
    initial(ctx, architecture=spec)
    unchanged_on_error(ctx, db, {"remove_component_ids": ["old"], "retirements": [
        {"component_id": "old", "milestone_id": "CHECK", "instruction": "Retire"}]}, "groups")


@pytest.mark.parametrize("groups", [[], [
    {"id": "group", "label": "Revised grouping", "member_node_ids": ["keep"]},
]])
def test_group_repair_and_component_retirement_share_one_atomic_delta(app, groups):
    ctx, db = stage(app)
    spec = architecture("old", "keep")
    spec["diagram"]["groups"] = [{"id": "group", "label": "Saved grouping", "member_node_ids": ["old", "keep"]}]
    initial(ctx, architecture=spec, milestones=[node(behaviors=[behavior("check", component_ids=["old"])])])
    before = db.get(ctx.project_id)
    checkpoints = len(db.saved_events)
    submit(ctx, {
        "remove_component_ids": ["old"], "architecture_groups": groups,
        "contracts": [contract(component_ids=["keep"])],
        "retirements": [{"component_id": "old", "milestone_id": "CHECK", "instruction": "Retire old module"}],
    })
    assert len(db.saved_events) == checkpoints + 1
    assert [n.id for n in db.project.architectures[-1].diagram.nodes] == ["keep"]
    expected_groups = [
        {"id": "group", "label": "Revised grouping", "description": "", "kind": "shared", "member_node_ids": ["keep"]}
    ] if groups else []
    assert [group.model_dump() for group in db.project.architectures[-1].diagram.groups] == expected_groups
    assert db.project.architectures[:-1] == before.architectures
    assert db.project.milestone("CHECK").migration_steps[-1].component_id == "old"
    assert db.project.milestone("CHECK").migration_steps[-1].from_revision == 1


@pytest.mark.parametrize("clear", [False, True])
def test_obsolete_risk_and_quality_lists_can_be_explicitly_replaced_or_cleared(app, clear):
    ctx, db = stage(app)
    old_quality = {"concern": "Scale", "scenario": "Old load", "measure": "Old target", "approach": "Old method"}
    initial(ctx, architecture=architecture("core", risks=["Obsolete deployment risk"], quality_scenarios=[old_quality],
                                           decisions=["Preserve unrelated decision"]))
    before = db.get(ctx.project_id)
    new_quality = {"concern": "Locality", "scenario": "Offline use", "measure": "Zero network", "approach": "Local resolver"}
    risks, scenarios = ([], []) if clear else (["Current path risk"], [new_quality])
    submit(ctx, {"risks": risks, "quality_scenarios": scenarios})
    current = db.project.architectures[-1]
    assert current.risks == risks
    assert [item.model_dump() for item in current.quality_scenarios] == scenarios
    assert current.decisions == before.architectures[-1].decisions
    assert current.diagram == before.architectures[-1].diagram
    assert db.project.architectures[:-1] == before.architectures


@pytest.mark.parametrize("links", [[], ["KEEP"]])
def test_slice_deletion_can_explicitly_replace_architecture_milestone_links(app, links):
    ctx, db = stage(app)
    spec = architecture("core")
    spec["diagram"]["milestone_ids"] = ["DROP", "KEEP"]
    initial(ctx, architecture=spec, milestones=[node("DROP"), node("KEEP")])
    before = db.get(ctx.project_id)
    submit(ctx, {"remove_slice_ids": ["DROP"], "architecture_milestone_ids": links})
    assert [m.id for m in db.project.milestones] == ["KEEP"]
    assert db.project.architectures[-1].diagram.milestone_ids == links
    assert db.project.architectures[:-1] == before.architectures
    assert db.project.behaviors[:len(before.behaviors)] == before.behaviors


def test_slice_deletion_without_explicit_architecture_link_repair_is_rejected(app):
    ctx, db = stage(app)
    spec = architecture("core")
    spec["diagram"]["milestone_ids"] = ["DROP", "KEEP"]
    initial(ctx, architecture=spec, milestones=[node("DROP"), node("KEEP")])
    unchanged_on_error(ctx, db, {"remove_slice_ids": ["DROP"]}, "milestone_ids")


@pytest.mark.parametrize("payload, error", [
    ({"architecture_groups": [{"id": "g", "label": "Unknown", "member_node_ids": ["missing"]}]}, "groups"),
    ({"architecture_milestone_ids": ["missing"]}, "milestone_ids"),
    ({"architecture_milestone_ids": ["CLI", "CLI"]}, "duplicate"),
    ({"architecture_groups": [
        {"id": "g", "label": "One", "member_node_ids": ["cli"]},
        {"id": "g", "label": "Two", "member_node_ids": ["parser"]},
    ]}, "分组 ID 重复"),
])
def test_invalid_advanced_architecture_repairs_are_atomic(app, payload, error):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    unchanged_on_error(ctx, db, payload, error)


@pytest.mark.parametrize("replacement", [[], ["moved_auth.py"]])
def test_moved_source_file_reference_can_be_replaced_or_cleared(app, repository, replacement):
    project = app.projects.create("Source references", repository=str(repository))
    ctx, db = stage(app, project)
    spec = architecture("core")
    spec["diagram"]["nodes"][0].update(source_refs=["auth.py"], source_ref_count=7)
    initial(ctx, architecture=spec)
    before = db.get(ctx.project_id)
    (repository / "auth.py").rename(repository / "moved_auth.py")
    # An unrelated upsert cannot silently erase the now-invalid old reference.
    unchanged_on_error(ctx, db, {"components": [component("core")]}, "源码不存在或不可读取")
    submit(ctx, {"components": [component("core", source_refs=replacement)]})
    current = db.project.architectures[-1].diagram.nodes[0]
    assert current.source_refs == replacement
    assert current.source_ref_count == len(replacement)
    assert db.project.architectures[:-1] == before.architectures
    assert db.project.architectures[0].diagram.nodes[0].source_ref_count == 7


@pytest.mark.parametrize("refs, error", [
    (["missing.py"], "源码不存在或不可读取"),
    (["../outside.py"], "源码不存在或不可读取"),
    (["symlink.py"], "源码不存在或不可读取"),
    (["auth.py", "auth.py"], "duplicate"),
])
def test_source_reference_replacements_retain_real_file_and_path_validation(app, repository, refs, error):
    outside = repository.parent / "outside.py"
    outside.write_text("# synthetic fixture\n")
    (repository / "symlink.py").symlink_to(outside)
    project = app.projects.create("Reference validation", repository=str(repository))
    ctx, db = stage(app, project)
    initial(ctx, architecture=architecture("core"))
    unchanged_on_error(ctx, db, {"components": [component("core", source_refs=refs)]}, error)


def test_full_ir_schema_never_advertises_null_but_fields_remain_optional():
    schema = DELTA_TOOL.schema()["function"]["parameters"]

    def check(node):
        if isinstance(node, dict):
            assert node.get("type") != "null"
            assert "default" not in node or node["default"] is not None
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    for field in ("target", "architecture_groups", "quality_scenarios", "risks"):
        assert field not in schema.get("required", [])
        with pytest.raises(ValidationError):
            PlanDelta.model_validate({field: None})
    assert "source_id" not in schema["$defs"]["RequirementDelta"]["required"]
    assert "title" in schema["$defs"]["SliceDelta"]["properties"]
    assert schema["$defs"]["SliceDelta"]["properties"]["title"]["type"] == "string"
    assert PlanDelta.model_validate({}).model_fields_set == set()


def test_invalid_source_quote_does_not_save_compilation_audit(app):
    ctx, db = stage(app)
    payload = complete_delta()
    payload["requirements"][0]["quote"] = "Invented quotation"
    unchanged_on_error(ctx, db, payload, "逐字")


def test_new_architecture_has_no_inferred_stack_or_summary(app):
    ctx, db = stage(app)
    unchanged_on_error(ctx, db, {"components": [component("c")]}, "architecture_summary and technologies")


def test_ambiguous_existing_contract_ownership_is_rejected_without_guessing(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("A"), node("B")])
    project = db.get(ctx.project_id)
    project.milestone("B").behavior_revision_ids = project.milestone("A").behavior_revision_ids
    with pytest.raises(ValueError, match="ambiguous ownership"):
        compile_plan_delta(project, {"slices": [{"id": "A", "title": "Changed"}]})


def test_historical_contract_restoration_still_requires_explicit_supported_key(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node(behaviors=[behavior("check"), behavior("step", acceptance_scope="milestone")])])
    previous = active_behaviors(db.project)["step"]
    submit(ctx, {"remove_contract_keys": ["step"]})
    unchanged_on_error(ctx, db, {"contracts": [contract("step")]}, "仅在历史中")
    submit(ctx, {"contracts": [contract("step")], "restore_contract_keys": ["step"]})
    assert active_behaviors(db.project)["step"].acceptance_scope == previous.acceptance_scope


def test_process_constraints_are_separate_from_product_requirements(app):
    text = INPUT + " Only plan; do not execute commands."
    ctx, db = stage(app, text=text)
    payload = complete_delta()
    payload["process_constraints"] = [{"id": "planning", "quote": "Only plan; do not execute commands.",
                                       "rule": "planning_only"}]
    submit(ctx, payload)
    assert [r.id for r in db.project.plan_contract.requirements] == ["r"]
    assert [p.id for p in db.project.plan_contract.process_constraints] == ["planning"]
    assert {rid for binding in db.project.plan_contract.bindings for rid in binding.requirement_ids} == {"r"}
    assert db.record["compilations"][0]["ir"]["process_constraints"] == payload["process_constraints"]


def test_failed_checkpoint_does_not_save_plan_or_audit(app, monkeypatch):
    ctx, db = stage(app)
    def fail(record):
        raise ConflictError("Injected checkpoint failure")
    monkeypatch.setattr(db.store, "save", fail)
    unchanged_on_error(ctx, db, complete_delta(), "checkpoint")
    assert not getattr(ctx, "candidate_ready", False)


def test_noop_only_records_compilation_audit_without_changing_plan(app):
    ctx, db = stage(app)
    submit(ctx, complete_delta())
    before = db.project.model_dump()
    checkpoints = len(db.saved_events)
    result = submit(ctx, {})
    assert result["status"] == "NO_PROGRESS"
    assert db.project.model_dump() == before
    assert len(db.saved_events) == checkpoints
    assert db.record["compilations"][-1]["ir"] == {}


def test_empty_upsert_lists_preserve_absent_architecture(app):
    ctx, db = stage(app)
    before = db.project.model_dump()
    payload = {name: [] for name in (
        "components", "relations", "remove_relations", "remove_component_ids", "retirements",
    )}
    compiled = compile_plan_delta(db.project, payload)
    assert compiled.patch.architecture is None
    assert submit(ctx, payload)["status"] == "NO_PROGRESS"
    assert db.project.model_dump() == before


def test_protocol_is_shallow_deterministic_and_small_single_contract_delta(app):
    ctx, db = stage(app)
    initial(ctx)
    payload = {"contracts": [contract(statement="Refined result")]}
    first = compile_plan_delta(db.project, payload)
    second = compile_plan_delta(db.project, payload)
    assert first.audit == second.audit
    assert DELTA_TOOL.name == "submit_plan_delta" and DELTA_TOOL.parameters is PlanDelta
    schema = compact_schema(DELTA_TOOL.schema())["function"]["parameters"]
    for definition in ("ContractDelta", "SliceDelta", "ComponentDelta"):
        assert not any("$ref" in field for field in schema["$defs"][definition]["properties"].values())
    assert not {"ArchitectureSpec", "PatchMilestone", "PatchBehavior"} & schema["$defs"].keys()
    assert "title" in schema["$defs"]["SliceDelta"]["properties"]
    assert len(json.dumps(payload, separators=(",", ":")).encode()) < 300
    assert encoded_size(compact_schema(DELTA_TOOL.schema())) <= 8192
