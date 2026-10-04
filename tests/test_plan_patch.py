"""One linked planning edit has one checkpoint and never a partial candidate."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.application.plan_patch import PATCH_TOOL, PlanPatch, propose_plan_patch
from evograph.application.plan_stage import StagedDatabase
from evograph.domain.models import Baseline, Evidence, now, uid
from evograph.domain.plan_contracts import IntentSource, active_behaviors, contract_findings
from evograph.domain.policies import current_evidence
from evograph.infrastructure.database import ConflictError
from pydantic import ValidationError

INPUT = "Build a local link checker for all Markdown links. Do not modify source files."


def stage(app, project=None, text=INPUT, source_id=None):
    project = project.model_copy(deep=True) if project else app.projects.create("Linked planner")
    source_id = source_id or uid()
    project.plan_contract.sources.append(IntentSource(id=source_id, text=text))
    record = app.unified.store.save({
        "id": source_id, "project": project.model_dump(), "created_at": now(),
        "base_revision": app.db.get(project.id).revision, "revision": project.revision,
        "status": "generating", "report": {}, "metrics": {},
    })
    db = StagedDatabase(app.db, app.unified.store, record, source_id)
    return ToolContext(project.id, SimpleNamespace(db=db)), db


def behavior(key="links", **changes):
    return {"key": key, "statement": f"Observable {key} result", "requirement_ids": ["r"],
            "mechanism": f"Implement {key} with a read-only Markdown AST traversal", **changes}


def node(mid="CHECK", **changes):
    return {"id": mid, "title": "Local checker", "intent": "Report broken links",
            "scope": ["parser.py"], "behaviors": [behavior(mid.lower())], **changes}


def architecture(*components, **changes):
    return {"summary": "Local read-only checker with explicit module ownership",
            "technologies": [{"area": "runtime", "choice": "Python", "rationale": "Local tool"}],
            "diagram": {"id": "system", "title": "Local modules", "nodes": [
                {"id": cid, "label": cid, "description": f"Owns {cid} behavior and data"}
                for cid in components]}, **changes}


def patch(ctx, **changes):
    return propose_plan_patch(ctx, PlanPatch.model_validate(changes))


def initial(ctx, **changes):
    return patch(ctx, **{"add_requirements": [{"id": "r", "quote": INPUT, "kind": "outcome"}],
                        "target": INPUT, "milestones": [node()], **changes})


def assert_unchanged(db, before, record, events):
    assert db.project.model_dump() == before
    assert db.record == record
    assert db.store.get(record["id"]) == record
    assert db.saved_events == events


def failing_patch(ctx, db, expected, **changes):
    before, record, events = db.project.model_dump(), deepcopy(db.record), deepcopy(db.saved_events)
    with pytest.raises((ValueError, ValidationError), match=expected):
        patch(ctx, **changes)
    assert_unchanged(db, before, record, events)


def test_new_plan_architecture_forward_dependencies_and_links_one_checkpoint(app, monkeypatch):
    ctx, db = stage(app)
    canonical = app.db.get(ctx.project_id).model_dump()
    writes = []
    save = db.store.save
    monkeypatch.setattr(db.store, "save", lambda record: (writes.append(deepcopy(record)), save(record))[1])
    spec = architecture("parser", "cli")
    spec["diagram"]["milestone_ids"] = ["CLI", "CORE"]
    result = initial(ctx, architecture=spec, milestones=[
        node("CLI", dependencies=["CORE"], dependency_reasons={"CORE": "Needs parsed links"},
             behaviors=[behavior("cli", component_ids=["cli"], requires_behavior_keys=["core"])]),
        node("CORE", behaviors=[behavior("core", component_ids=["parser"])]),
    ])
    p = db.get(ctx.project_id)
    assert len(writes) == len(db.saved_events) == len(p.plans) == len(p.targets) == 1
    assert p.revision == 1
    assert contract_findings(p) == result["findings"] == []
    assert ctx.candidate_ready is True
    assert p.milestone("CORE").architecture_components == ["parser"]
    assert p.milestone("CLI").dependencies == ["CORE"]
    assert all(m.architecture_revision == 1 for m in p.milestones)
    assert app.db.get(ctx.project_id).model_dump() == canonical


def test_noop_repeated_patch_preserves_ids_histories_and_checkpoint_count(app):
    ctx, db = stage(app)
    initial(ctx)
    before = db.project.model_dump()
    result = patch(ctx, target=INPUT, milestones=[node()])
    assert result["status"] == "NO_PROGRESS"
    assert db.project.model_dump() == before
    assert len(db.saved_events) == 1


@pytest.mark.parametrize("field", ["statement", "mechanism", "requirement_ids"])
def test_contract_edits_revise_behavior_and_stale_evidence_without_erasing_it(app, field):
    ctx, db = stage(app)
    initial(ctx)
    before = db.get(ctx.project_id)
    old = active_behaviors(before)["check"]
    baseline = Baseline(number=1, commit="abc", fingerprint="fp", file_count=1, complete=True)
    before.baselines = [baseline]
    before.milestone("CHECK").status = "VERIFIED_COMPLETE"
    before.milestone("CHECK").pinned_baseline = baseline.id
    before.evidence = [Evidence(
        milestone_id="CHECK", behavior_revision_ids=[old.id], baseline_id=baseline.id,
        fingerprint=baseline.fingerprint, command=[], result="PASS", output="Old evidence", duration=0,
    )]
    assert current_evidence(before, old.id)
    ctx, db = stage(app, before)
    changes = {field: ["r", "extra"] if field == "requirement_ids" else "Changed exact contract"}
    additions = [{"id": "extra", "quote": "Do not modify source files.", "kind": "constraint"}] if field == "requirement_ids" else []
    patch(ctx, add_requirements=additions, milestones=[node(behaviors=[behavior("check", **changes)])])
    after = db.get(ctx.project_id)
    revised = active_behaviors(after)["check"]
    assert revised.id != old.id and revised.supersedes == old.id
    assert revised.version == old.version + 1
    assert after.behaviors[:len(before.behaviors)] == before.behaviors
    assert after.targets[:len(before.targets)] == before.targets
    assert after.evidence == before.evidence
    assert after.baselines == before.baselines
    assert after.milestone("CHECK").status == "PLANNED"
    assert after.milestone("CHECK").pinned_baseline is None
    assert current_evidence(after, revised.id) is None
    assert after.plan_contract.bindings[0].behavior_revision_id == revised.id


def test_omitted_dependency_scope_binding_links_and_unrelated_nodes_are_retained(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core", "cli"), milestones=[
        node("CORE", behaviors=[behavior("core", component_ids=["core"])]),
        node("CLI", dependencies=["CORE"], dependency_reasons={"CORE": "Needs core output"},
             behaviors=[behavior("cli", acceptance_scope="milestone", component_ids=["cli"],
                                 requires_behavior_keys=["core"])]),
    ])
    before = db.get(ctx.project_id)
    patch(ctx, milestones=[node("CLI", title="Refined local CLI")])
    after = db.get(ctx.project_id)
    assert after.milestone("CORE") == before.milestone("CORE")
    assert after.architectures == before.architectures
    assert after.milestone("CLI").dependencies == ["CORE"]
    assert after.milestone("CLI").dependency_reasons == {"CORE": "Needs core output"}
    assert active_behaviors(after)["cli"].acceptance_scope == "milestone"
    assert after.plan_contract.bindings == before.plan_contract.bindings
    assert after.behaviors == before.behaviors


def test_new_component_and_new_slice_share_patch_preserving_architecture_rationale(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("core", decisions=["Keep execution local"], risks=["Paths may be invalid"]),
            milestones=[node(behaviors=[behavior("check", component_ids=["core"])])])
    before = db.get(ctx.project_id)
    patch(ctx, architecture=architecture("core", "report"), milestones=[
        node("REPORT", dependencies=["CHECK"], dependency_reasons={"CHECK": "Consumes diagnostics"},
             behaviors=[behavior("report", component_ids=["report"], requires_behavior_keys=["check"])])])
    after = db.get(ctx.project_id)
    assert len(after.architectures) == 2
    assert after.architectures[0] == before.architectures[0]
    assert after.architectures[-1].decisions == ["Keep execution local"]
    assert after.architectures[-1].risks == ["Paths may be invalid"]
    assert after.milestone("REPORT").architecture_components == ["report"]
    assert contract_findings(after) == []


def test_retire_component_into_new_migration_owner_in_same_patch(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("old"), milestones=[
        node(behaviors=[behavior("check", component_ids=["old"])])])
    before = db.get(ctx.project_id)
    patch(ctx, architecture=architecture("new", retirements=[
        {"component_id": "old", "milestone_id": "MIGRATE", "instruction": "Move local data and remove old adapter"}]),
        milestones=[
            node("MIGRATE", behaviors=[behavior("migrate", component_ids=["new"])]),
            node(behaviors=[behavior("check", component_ids=["new"])])])
    after = db.get(ctx.project_id)
    assert after.architectures[0] == before.architectures[0]
    assert after.milestone("CHECK").architecture_components == ["new"]
    assert after.milestone("MIGRATE").migration_steps[0].component_id == "old"
    assert after.milestone("MIGRATE").migration_steps[0].from_revision == 1
    assert contract_findings(after) == []


def test_dependent_removals_and_explicit_surviving_link_updates_are_order_independent(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[
        node("A"), node("B", dependencies=["A"], dependency_reasons={"A": "Consumes A"},
                        behaviors=[behavior("b", requires_behavior_keys=["a"])]),
        node("C", dependencies=["B"], dependency_reasons={"B": "Consumes B"},
             behaviors=[behavior("c", requires_behavior_keys=["b"])])])
    before = db.get(ctx.project_id)
    patch(ctx, remove_milestone_ids=["A", "B"], milestones=[
        node("C", dependencies=[], behaviors=[behavior("c", requires_behavior_keys=[])])])
    after = db.get(ctx.project_id)
    assert [m.id for m in after.milestones] == ["C"]
    assert after.milestone("C").dependencies == []
    assert after.milestone("C").dependency_reasons == {}
    assert after.behaviors[:len(before.behaviors)] == before.behaviors
    assert [b.behavior_key for b in after.plan_contract.bindings] == ["c"]
    assert contract_findings(after) == []


@pytest.mark.parametrize("repair_dependency", [False, True])
def test_removal_never_silently_drops_surviving_graph_or_contract_links(app, repair_dependency):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("A"), node("B", dependencies=["A"],
        dependency_reasons={"A": "Consumes A"}, behaviors=[behavior("b", requires_behavior_keys=["a"])])])
    changes = {"milestones": [node("B", dependencies=[])]} if repair_dependency else {}
    failing_patch(ctx, db, "future_control" if repair_dependency else "不存在",
                  remove_milestone_ids=["A"], **changes)


def test_active_behavior_moves_preserve_scope_without_false_restoration_requirement(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("OLD", behaviors=[behavior("step", acceptance_scope="milestone")]), node("GOAL")])
    previous = active_behaviors(db.project)["step"]
    result = patch(ctx, remove_milestone_ids=["OLD"], milestones=[node("NEW", behaviors=[behavior("step")])])
    restored = active_behaviors(db.project)["step"]
    assert restored.owner == "NEW" and restored.acceptance_scope == "milestone"
    assert restored.supersedes == previous.id
    assert result["restored_inactive_behaviors"] == []


def test_historical_restoration_requires_explicit_intent_and_retains_scope_history(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node(behaviors=[behavior("check"), behavior("step", acceptance_scope="milestone")])])
    historical = active_behaviors(db.project)["step"]
    patch(ctx, milestones=[node()])
    failing_patch(ctx, db, "仅在历史中", milestones=[node(behaviors=[behavior("check"), behavior("step")])])
    result = patch(ctx, milestones=[node(restore_inactive_behavior_keys=["step"],
                                         behaviors=[behavior("check"), behavior("step")])])
    current = active_behaviors(db.project)["step"]
    assert current.id == historical.id and current.acceptance_scope == "milestone"
    assert result["restored_inactive_behaviors"][0]["saved_revision_id"] == current.id


def test_requirements_retirement_and_addition_keep_exact_source_history(app):
    ctx, db = stage(app)
    initial(ctx)
    before = db.get(ctx.project_id)
    changed_input = "Replace link checking with Markdown heading validation. Still do not modify source files."
    ctx, db = stage(app, before, text=changed_input, source_id="replacement")
    patch(ctx, retire_requirements=[{"id": "r", "quote": "Replace link checking", "reason": "Explicitly replaced outcome"}],
          add_requirements=[{"id": "headings", "quote": changed_input, "kind": "outcome"}],
          target=changed_input, milestones=[node(behaviors=[behavior("headings", requirement_ids=["headings"])])])
    after = db.get(ctx.project_id)
    old, new = after.plan_contract.requirements
    assert old.quote == INPUT and old.source_id == before.plan_contract.requirements[0].source_id
    assert not old.active and old.retired_by.source_id == "replacement"
    assert new.quote == changed_input and new.source_id == "replacement"
    assert after.plan_contract.sources[:len(before.plan_contract.sources)] == before.plan_contract.sources
    assert after.behaviors[:len(before.behaviors)] == before.behaviors


@pytest.mark.parametrize("changes, error", [
    ({"add_requirements": [{"id": "r", "quote": "all Markdown links", "kind": "outcome"}]}, "不可覆盖"),
    ({"add_requirements": [{"id": "invented", "quote": "Check only simple links", "kind": "outcome"}]}, "逐字"),
    ({"retire_requirements": [{"id": "r", "quote": INPUT, "reason": "Just added"}]}, "同一输入"),
    ({"remove_milestone_ids": ["CHECK"]}, "uncovered_requirement"),
    ({"milestones": [node("A", dependencies=["B"], dependency_reasons={"B": "Needs B"}),
                     node("B", dependencies=["A"], dependency_reasons={"A": "Needs A"})]}, "环"),
    ({"milestones": [node("SRC_forged")]}, "保留命名空间"),
    ({"milestones": [node(behaviors=[behavior("check", component_ids=["missing"])])]}, "架构组件不存在"),
    ({"milestones": [node(behaviors=[behavior("check", requirement_ids=["missing"])])]}, "invalid_requirement_link"),
    ({"milestones": [node("CHECK", behaviors=[behavior("check", requires_behavior_keys=["later"])]),
                     node("LATER")]}, "future_control"),
    ({"milestones": [node(), node()]}, "不能重复"),
    ({"milestones": [node()], "remove_milestone_ids": ["CHECK"]}, "同时更新和移除"),
])
def test_invalid_patch_is_fully_atomic(app, changes, error):
    ctx, db = stage(app)
    initial(ctx)
    failing_patch(ctx, db, error, **changes)


@pytest.mark.parametrize("field", ["evidence", "baselines", "repository", "source_milestones", "revision"])
def test_patch_schema_rejects_out_of_scope_fields(app, field):
    ctx, db = stage(app)
    failing_patch(ctx, db, "Extra inputs", **{field: []})


def test_bounded_schema_and_non_candidate_write_guard(app):
    with pytest.raises(ValidationError):
        PlanPatch(milestones=[node(str(n)) for n in range(25)])
    with pytest.raises(ValidationError):
        PlanPatch(remove_milestone_ids=[str(n) for n in range(25)])
    p = app.projects.create("Canonical only")
    with pytest.raises(ValueError, match="候选"):
        patch(ToolContext(p.id, app), milestones=[node()])
    assert PATCH_TOOL.name == "propose_plan_patch"
    assert PATCH_TOOL.parameters is PlanPatch


@pytest.mark.parametrize("operation", ["upsert", "remove", "architecture"])
def test_lease_protection_survives_private_compilation(app, operation):
    ctx, db = stage(app)
    initial(ctx)
    p = db.get(ctx.project_id)
    p.milestone("CHECK").lease_active = True
    ctx, db = stage(app, p)
    changes = {"upsert": {"milestones": [node(title="Refined")]},
               "remove": {"remove_milestone_ids": ["CHECK"]},
               "architecture": {"architecture": architecture("core")}}[operation]
    failing_patch(ctx, db, "释放", **changes)


def test_failed_checkpoint_never_advances_candidate_or_ready_flag(app, monkeypatch):
    ctx, db = stage(app)
    before, record, events = db.project.model_dump(), deepcopy(db.record), deepcopy(db.saved_events)
    def fail(record):
        raise ConflictError("Injected checkpoint failure")
    monkeypatch.setattr(db.store, "save", fail)
    with pytest.raises(ConflictError, match="checkpoint"):
        initial(ctx)
    assert_unchanged(db, before, record, events)
    assert not getattr(ctx, "candidate_ready", False)


def test_complete_architecture_replacement_can_preview_more_than_40_total_ids(app):
    ctx, db = stage(app)
    old_ids = [f"old_{n}" for n in range(24)]
    new_ids = [f"new_{n}" for n in range(24)]
    initial(ctx, architecture=architecture(*old_ids), milestones=[
        node(behaviors=[behavior("check", component_ids=["old_0"])])])
    patch(ctx, architecture=architecture(*new_ids, retirements=[
        {"component_id": cid, "milestone_id": "MIGRATE", "instruction": "Remove obsolete module"}
        for cid in old_ids]), milestones=[
            node(behaviors=[behavior("check", component_ids=["new_0"])]),
            node("MIGRATE", behaviors=[behavior("migrate", component_ids=new_ids)])])
    p = db.get(ctx.project_id)
    assert {n.id for n in p.architectures[-1].diagram.nodes} == set(new_ids)
    assert len(p.milestone("MIGRATE").migration_steps) == len(old_ids)
    assert contract_findings(p) == []


def test_failed_architecture_retirement_does_not_expose_private_graph_edits(app):
    ctx, db = stage(app)
    initial(ctx, architecture=architecture("old"), milestones=[
        node(behaviors=[behavior("check", component_ids=["old"])])])
    failing_patch(ctx, db, "retirements", architecture=architecture("new"), milestones=[
        node("NEW", behaviors=[behavior("new", component_ids=["new"])])])


def test_active_key_swap_is_order_independent_without_inventing_restoration(app):
    ctx, db = stage(app)
    initial(ctx, milestones=[node("A"), node("B")])
    before = db.get(ctx.project_id)
    patch(ctx, milestones=[node("A", behaviors=[behavior("b")]), node("B", behaviors=[behavior("a")])])
    assert active_behaviors(db.project)["b"].owner == "A"
    assert active_behaviors(db.project)["a"].owner == "B"
    assert db.project.behaviors[:len(before.behaviors)] == before.behaviors


def test_offline_incremental_patch_and_tool_schema_byte_bounds():
    import json
    def byte_count(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    all_nodes = [node(f"M{n:02d}") for n in range(24)]
    full = {"target": INPUT, "milestones": all_nodes}
    incremental = {"milestones": [node("M12", title="Refined read-only diagnostics")]}
    # Fixture sizes are observable byte budgets, not estimates of provider tokens.
    assert byte_count(incremental) < byte_count(full) / 20
    assert byte_count(PATCH_TOOL.schema()) < 12500
    assert byte_count(incremental) < 400


def test_active_move_retains_active_scope_even_with_newer_unused_historical_revision(app):
    from evograph.domain.models import BehaviorRevision
    ctx, db = stage(app)
    initial(ctx, milestones=[node("OLD", behaviors=[behavior("step", acceptance_scope="milestone")]), node("GOAL")])
    p = db.get(ctx.project_id)
    prior = active_behaviors(p)["step"]
    historical = BehaviorRevision(behavior_key="step", statement="Unused future contract", owner="OLD",
                                  version=2, supersedes=prior.id, acceptance_scope="target")
    p.behaviors.append(historical)
    ctx, db = stage(app, p)
    patch(ctx, remove_milestone_ids=["OLD"], milestones=[node("NEW", behaviors=[behavior("step")])])
    current = active_behaviors(db.project)["step"]
    assert current.acceptance_scope == "milestone" and current.owner == "NEW"
    assert current.supersedes == historical.id and current.version == 3


def test_title_only_edit_keeps_verified_status_and_evidence_current(app):
    ctx, db = stage(app)
    initial(ctx)
    p = db.get(ctx.project_id)
    baseline = Baseline(number=1, commit="abc", fingerprint="fp", file_count=1, complete=True)
    p.baselines = [baseline]
    m = p.milestone("CHECK")
    m.status, m.pinned_baseline = "VERIFIED_COMPLETE", baseline.id
    for obligation in m.obligations:
        obligation.resolved = True
        obligation.note = "Existing proof"
    p.evidence = [Evidence(milestone_id=m.id, behavior_revision_ids=m.behavior_revision_ids,
                          baseline_id=baseline.id, fingerprint=baseline.fingerprint,
                          command=[], result="PASS", output="Existing proof", duration=0)]
    ctx, db = stage(app, p)
    patch(ctx, milestones=[node(title="Precise checker title")])
    current = db.get(ctx.project_id)
    assert current.behaviors == p.behaviors and current.evidence == p.evidence
    assert current.milestone("CHECK").status == "VERIFIED_COMPLETE"
    assert current.milestone("CHECK").pinned_baseline == baseline.id
    assert current.milestone("CHECK").obligations == m.obligations
    assert current_evidence(current, m.behavior_revision_ids[0]) == p.evidence[0]
