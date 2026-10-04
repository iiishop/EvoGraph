"""Target review is the finalizer's read-only decision, never a promise of a save."""

import json

import pytest
from conftest import proposal
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.design_review import current_review
from evograph.application.design_workflow import DESIGN_WORKFLOW
from evograph.domain.design_review import review_design
from evograph.domain.models import BehaviorRevision, Milestone, Project, TargetVersion
from evograph.domain.target_contract import target_finalization, target_finalization_preview


@pytest.mark.parametrize("statement_changed,behavior_changed", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_review_matches_finalization_without_writing(app, planned, statement_changed, behavior_changed):
    if statement_changed:
        app.graph.target(planned.id, "A revised final goal")
    if behavior_changed:
        app.graph.upsert(
            planned.id, proposal(statement="Revised observable behavior").milestones[0], False,
        )
    before = app.db.get(planned.id)
    snapshot = before.model_dump()
    events = app.db.events(planned.id)
    ctx = ToolContext(planned.id, app, before_snapshot=planned)
    first = current_review(ctx)["target_finalization"]
    repeated = current_review(ctx)["target_finalization"]
    changed = statement_changed or behavior_changed

    assert first == repeated == review_design(before)["target_finalization"]
    assert first["status"] == ("new_version_projected" if changed else "unchanged")
    assert first["project_revision"] == before.revision
    assert first["state"] == "projection_not_saved"
    assert first["statement_changed"] is statement_changed
    assert first["required_behavior_ids_changed"] is behavior_changed
    assert first["would_create_target_version"] is changed
    assert first["latest_committed_target_version"] == 1
    assert first["expected_target_version_if_finalized"] == 1 + int(changed)
    assert first["committed_required_behavior_count"] == 1
    assert first["projected_required_behavior_count"] == 1
    assert "concurrent edits" in first["basis"]
    assert "not committed or guaranteed" in first["basis"]
    assert app.db.get(planned.id).model_dump() == snapshot
    assert app.db.events(planned.id) == events

    decision = target_finalization(before)
    assert app.graph.finalize(planned.id) == []
    after = app.db.get(planned.id)
    assert after.targets[-1].number == first["expected_target_version_if_finalized"]
    assert after.targets[-1].statement == decision.statement
    assert after.targets[-1].required_behavior_ids == decision.required_behavior_ids
    assert len(after.targets) == len(before.targets) + int(changed)
    assert after.targets[: len(before.targets)] == before.targets
    assert after.behaviors == before.behaviors
    assert after.evidence == before.evidence
    assert after.milestones == before.milestones
    assert after.target_draft is None
    assert after.plans[-1].target_version == after.targets[-1].number
    if not changed:
        assert after.model_dump() == snapshot
    after_snapshot = after.model_dump()
    assert app.graph.finalize(planned.id) == []
    assert app.db.get(planned.id).model_dump() == after_snapshot


@pytest.mark.parametrize("draft", [None, "", "Login V1"])
def test_unchanged_draft_cleanup_keeps_existing_save_semantics(app, planned, draft):
    if draft is not None:
        app.graph.target(planned.id, draft)
    before = app.db.get(planned.id)
    assert not target_finalization_preview(before)["would_create_target_version"]
    app.graph.finalize(planned.id)
    after = app.db.get(planned.id)
    assert after.targets == before.targets
    assert after.target_draft is None
    assert after.revision == before.revision + int(draft is not None)
    if draft is not None:
        assert app.db.events(planned.id)[0]["kind"] == "target_unchanged"


@pytest.mark.parametrize("description,draft,has_milestone,expected_statement", [
    ("Description", None, False, None),
    ("Description", "", False, None),
    ("Description", "Draft", False, "Draft"),
    ("Description", " ", False, " "),
    ("Description", None, True, "Description"),
    ("", None, True, "Named project"),
    ("Description", "", True, "Description"),
    ("Description", "Draft", True, "Draft"),
])
def test_no_target_and_initial_target_defaults(
    app, description, draft, has_milestone, expected_statement,
):
    project = app.projects.create("Named project", description=description)
    if has_milestone:
        node = proposal().milestones[0]
        node.behaviors[0].acceptance_scope = "milestone"
        app.graph.upsert(project.id, node, True)
    if draft is not None:
        app.graph.target(project.id, draft)
    before = app.db.get(project.id)
    preview = review_design(before)["target_finalization"]
    assert preview["latest_committed_target_version"] is None
    assert preview["expected_target_version_if_finalized"] == (
        1 if expected_statement is not None else None
    )
    assert preview["projected_required_behavior_count"] == 0
    assert preview["required_behavior_ids_changed"] is False
    assert preview["statement_changed"] is (expected_statement is not None)
    assert preview["status"] == (
        "new_version_projected" if expected_statement is not None else "no_target"
    )
    app.graph.finalize(project.id)
    after = app.db.get(project.id)
    if expected_statement is None:
        assert after.model_dump() == before.model_dump()
    else:
        assert after.targets[-1].statement == expected_statement
        assert after.targets[-1].required_behavior_ids == []
        assert after.targets[-1].number == 1


def test_review_tool_recomputes_after_later_edit_instead_of_reusing_old_version(app, planned):
    spec = tools()["review_design"]
    ctx = ToolContext(planned.id, app)
    old = spec.handler(ctx, spec.parameters())["target_finalization"]
    app.graph.target(planned.id, "New final goal")
    new = spec.handler(ctx, spec.parameters())["target_finalization"]
    assert old["status"] == "unchanged"
    assert new["status"] == "new_version_projected"
    assert old["project_revision"] < new["project_revision"]
    assert old["expected_target_version_if_finalized"] == 1
    assert new["expected_target_version_if_finalized"] == 2
    assert app.db.get(planned.id).targets[-1].number == 1


@pytest.mark.parametrize("edit", ["remove", "add", "scope"])
def test_required_membership_changes_independently_of_statement(app, planned, edit):
    if edit == "remove":
        app.graph.remove(planned.id, "M01")
    elif edit == "add":
        app.graph.upsert(planned.id, proposal(node="M02", key="new.behavior").milestones[0], True)
    else:
        node = proposal().milestones[0]
        node.behaviors[0].acceptance_scope = "milestone"
        app.graph.upsert(planned.id, node, False)
    before = app.db.get(planned.id)
    preview = target_finalization_preview(before)
    assert preview["statement_changed"] is False
    assert preview["required_behavior_ids_changed"] is True
    assert preview["would_create_target_version"] is True
    assert preview["projected_required_behavior_count"] == (2 if edit == "add" else 0)
    app.graph.finalize(planned.id)
    after = app.db.get(planned.id)
    assert after.targets[-1].number == preview["expected_target_version_if_finalized"]
    assert len(after.targets[-1].required_behavior_ids) == preview["projected_required_behavior_count"]


def source_node(behavior_id):
    return Milestone(
        id="SRC_1", title="Source capability", intent="Observed source",
        origin="source", source_baseline_id="baseline", behavior_revision_ids=[behavior_id],
    )


def test_source_historical_latest_and_local_revisions_do_not_replace_active_target(app, planned):
    project = app.db.get(planned.id)
    active = project.behaviors[0]
    newer = active.model_copy(update={"id": "historical-latest", "version": 2})
    source = active.model_copy(update={"id": "source-only", "owner": "SRC_1"})
    removed = active.model_copy(update={"id": "removed", "owner": "removed"})
    local = active.model_copy(update={"id": "local", "acceptance_scope": "milestone"})
    project.behaviors.extend([newer, source, removed, local])
    project.source_milestones = [source_node(source.id)]
    project.milestones[0].behavior_revision_ids.append(local.id)
    app.db.save(project, "fixture_lifecycle")
    before = app.db.get(project.id)
    assert target_finalization(before).required_behavior_ids == [active.id]
    preview = review_design(before)["target_finalization"]
    assert preview["status"] == "unchanged"
    assert preview["projected_required_behavior_count"] == 1
    app.graph.finalize(project.id)
    assert app.db.get(project.id).model_dump() == before.model_dump()


def test_source_only_graph_does_not_create_a_target():
    source = BehaviorRevision(
        id="source", behavior_key="source", version=1, statement="Observed", owner="SRC_1",
    )
    project = Project(name="Source only", behaviors=[source], source_milestones=[source_node(source.id)])
    assert target_finalization(project) is None
    assert review_design(project)["target_finalization"]["status"] == "no_target"


@pytest.mark.parametrize("reorder", ["milestones", "behavior_ids"])
def test_order_sensitive_comparison_preserves_finalizer_generation_order(app, planned, reorder):
    node = proposal(node="M02", key="second").milestones[0]
    node.behaviors.append(node.behaviors[0].model_copy(update={"key": "third"}))
    app.graph.upsert(planned.id, node, True)
    app.graph.finalize(planned.id)
    project = app.db.get(planned.id)
    old_ids = project.targets[-1].required_behavior_ids[:]
    if reorder == "milestones":
        project.milestones.reverse()
    else:
        project.milestone("M02").behavior_revision_ids.reverse()
    # Storage order of the history itself must not determine generation order.
    project.behaviors.reverse()
    app.db.save(project, "fixture_order")
    before = app.db.get(planned.id)
    expected_ids = [bid for node in before.milestones for bid in node.behavior_revision_ids]
    assert set(expected_ids) == set(old_ids) and expected_ids != old_ids
    preview = review_design(before)["target_finalization"]
    assert preview["required_behavior_ids_changed"] is True
    assert preview["statement_changed"] is False
    app.graph.finalize(planned.id)
    after = app.db.get(planned.id)
    assert after.targets[-1].required_behavior_ids == expected_ids
    assert after.targets[-1].number == preview["expected_target_version_if_finalized"]


def test_legacy_target_numbering_uses_existing_length_rule():
    project = Project(
        name="Imported", targets=[TargetVersion(number=9, statement="Current", required_behavior_ids=[])],
    )
    assert target_finalization_preview(project)["expected_target_version_if_finalized"] == 9
    project.target_draft = "Changed"
    preview = target_finalization_preview(project)
    assert preview["latest_committed_target_version"] == 9
    assert preview["expected_target_version_if_finalized"] == 2


def test_legacy_empty_committed_statement_and_duplicate_ids_keep_exact_comparison(planned):
    planned.targets[-1].statement = ""
    planned.description = "Do not replace the committed empty statement"
    planned.milestones[0].behavior_revision_ids *= 2
    decision = target_finalization(planned)
    assert decision.statement == ""
    assert decision.statement_changed is False
    assert decision.required_behavior_ids == planned.targets[-1].required_behavior_ids * 2
    assert decision.required_behavior_ids_changed is True
    assert decision.creates_version is True
    planned.targets[-1].required_behavior_ids *= 2
    assert target_finalization_preview(planned)["status"] == "unchanged"


def test_missing_active_reference_withholds_projection_and_preserves_finalizer_failure(app, planned):
    project = app.db.get(planned.id)
    project.milestones[0].behavior_revision_ids.append("missing")
    app.db.save(project, "fixture_missing")
    before = app.db.get(planned.id)
    report = review_design(before)
    preview = report["target_finalization"]
    assert preview["status"] == "unavailable"
    for key in (
        "statement_changed", "required_behavior_ids_changed", "would_create_target_version",
        "expected_target_version_if_finalized", "projected_required_behavior_count",
    ):
        assert preview[key] is None
    assert any(item["code"] == "invalid_behavior" for item in report["findings"])
    with pytest.raises(KeyError, match="missing"):
        app.graph.finalize(planned.id)
    assert app.db.get(planned.id).model_dump() == before.model_dump()


def test_preview_stays_compact_and_does_not_alias_project_data(planned):
    planned.target_draft = "Very long target " * 10000
    planned.targets[-1].required_behavior_ids = [f"historical-{i}" for i in range(10000)]
    before = planned.model_dump()
    preview = review_design(planned)["target_finalization"]
    assert len(json.dumps(preview).encode()) < 1400
    assert preview["committed_required_behavior_count"] == 10000
    assert "required_behavior_ids" not in preview and "statement" not in preview
    decision = target_finalization(planned)
    decision.required_behavior_ids.clear()
    preview["latest_committed_target_version"] = 9000
    assert planned.model_dump() == before


def test_target_save_failure_retains_prior_normalization_and_draft(app, planned, monkeypatch):
    for mid in ("M02", "M03"):
        app.graph.upsert(planned.id, proposal(node=mid, key=mid).milestones[0], True)
    app.graph.dependency(planned.id, "M01", "M02", "Consumes M01")
    app.graph.dependency(planned.id, "M02", "M03", "Consumes M02")
    app.graph.dependency(planned.id, "M01", "M03", "Consumes M01 transitively")
    app.graph.target(planned.id, "Pending target")
    before = app.db.get(planned.id)
    preview = review_design(before)["target_finalization"]
    real_save = app.db.save

    def fail_target_save(project, kind, detail=""):
        if kind == "target_committed":
            raise RuntimeError("Fixture target save failure")
        return real_save(project, kind, detail)

    monkeypatch.setattr(app.db, "save", fail_target_save)
    with pytest.raises(RuntimeError, match="Fixture target save failure"):
        app.graph.finalize(planned.id)
    after = app.db.get(planned.id)
    assert preview["would_create_target_version"] is True
    assert after.targets == before.targets
    assert after.target_draft == before.target_draft == "Pending target"
    assert after.milestone("M03").dependencies == ["M02"]
    assert after.revision == before.revision + 1
    assert app.db.events(planned.id)[0]["kind"] == "dependencies_reduced"


def test_reporting_guidance_separates_statement_and_contract_without_claiming_commit():
    description = tools()["review_design"].description
    assert "target_finalization separates statement changes" in description
    assert "required behavior revision-ID changes" in description
    assert "reviewed state is not edited again and finalization succeeds" in description
    assert "never report it as already committed or guaranteed" in description
    workflow = " ".join(DESIGN_WORKFLOW.split())
    assert "using target_finalization" in workflow
    assert "expected version only conditionally on the reviewed state" in workflow
    assert "remaining unchanged and finalization succeeding" in workflow


def test_concurrent_edit_can_invalidate_projection_before_target_save(app, planned, monkeypatch):
    from evograph.infrastructure.database import ConflictError

    app.graph.target(planned.id, "Reviewed target")
    before = app.db.get(planned.id)
    preview = review_design(before)["target_finalization"]
    real_save = app.db.save

    def concurrent_edit(project, kind, detail=""):
        if kind == "target_committed":
            newer = app.db.get(project.id)
            newer.target_draft = "Concurrent target"
            real_save(newer, "concurrent_edit")
        return real_save(project, kind, detail)

    monkeypatch.setattr(app.db, "save", concurrent_edit)
    with pytest.raises(ConflictError):
        app.graph.finalize(planned.id)
    after = app.db.get(planned.id)
    assert after.targets == before.targets
    assert after.target_draft == "Concurrent target"
    assert after.revision > preview["project_revision"]
    assert after.targets[-1].number != preview["expected_target_version_if_finalized"]
