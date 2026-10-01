"""Pending architecture association must not erase mappings or bypass execution checks."""

import pytest
from conftest import apply_proposal, proposal
from evograph.domain.design_review import review_design
from evograph.domain.models import ArchitectureSpec, Evidence, ProposedMilestone
from evograph.domain.policies import current_evidence, readiness


def seed_architecture(app, project_id):
    app.design.update(
        project_id,
        ArchitectureSpec(
            summary="Existing API and background worker",
            technologies=[{"area": "API", "choice": "Python", "rationale": "Existing stack"}],
            diagram={
                "id": "system",
                "title": "System",
                "nodes": [{"id": "api", "label": "API"}, {"id": "worker", "label": "Worker"}],
            },
        ),
    )


def save_node(app, project_id, node, route, *, create=False):
    if route == "incremental":
        app.graph.upsert(project_id, node, create=create)
        app.graph.finalize(project_id)
    else:
        plan = proposal()
        plan.milestones = [node]
        apply_proposal(app, app.db.get(project_id), plan)
    return app.db.get(project_id)


@pytest.mark.parametrize("route", ["incremental", "full-plan"])
def test_new_and_previously_unmapped_slices_save_with_advisory_and_obligations(app, route):
    project = app.projects.create("New unrelated slice")
    seed_architecture(app, project.id)
    architecture = app.db.get(project.id).architectures
    node = proposal().milestones[0]
    created = save_node(app, project.id, node, route, create=True)
    original = created.milestone(node.id)
    node.title = "Refined unrelated slice"
    after = save_node(app, project.id, node, route)
    saved = after.milestone(node.id)
    assert saved.title == node.title
    assert saved.architecture_components == []
    assert saved.architecture_revision == 1
    assert saved.behavior_revision_ids == original.behavior_revision_ids
    assert saved.obligations == original.obligations
    assert len([o for o in saved.obligations if o.id == "architecture"]) == 1
    assert not next(o for o in saved.obligations if o.id == "architecture").resolved
    assert not readiness(after, node.id)["safe_to_execute"]
    findings = {f["code"]: f for f in review_design(after)["findings"]}
    assert findings["unmapped_delivery"]["severity"] == "review"
    assert "待关联" in findings["unmapped_delivery"]["message"]
    assert "不得覆盖用户本轮不做架构" in findings["unmapped_delivery"]["action"]
    assert "stale_architecture" not in findings
    assert after.architectures == architecture
    assert not after.evidence


@pytest.mark.parametrize("route", ["incremental", "full-plan"])
@pytest.mark.parametrize("mapping", ["omitted", "empty"])
def test_mapping_omission_or_empty_preserves_verified_identity_and_history(
    app, planned, route, mapping
):
    seed_architecture(app, planned.id)
    node = proposal().milestones[0]
    node.architecture_components = ["api"]
    app.graph.upsert(planned.id, node, create=False)
    before = app.db.get(planned.id)
    milestone = before.milestone(node.id)
    milestone.status = "VERIFIED_COMPLETE"
    milestone.pinned_baseline = before.baseline.id
    for obligation in milestone.obligations:
        obligation.resolved = True
        obligation.note = "Existing investigation evidence"
        obligation.fingerprint = before.baseline.fingerprint
    evidence = Evidence(
        milestone_id=node.id,
        behavior_revision_ids=milestone.behavior_revision_ids,
        baseline_id=before.baseline.id,
        fingerprint=before.baseline.fingerprint,
        architecture_revision=milestone.architecture_revision,
        command=["fixture"],
        result="PASS",
        output="Existing acceptance evidence",
        duration=0,
    )
    before.evidence.append(evidence)
    app.db.save(before, "verified_fixture")
    before = app.db.get(planned.id)
    payload = node.model_dump()
    payload["title"] = "Renamed delivery"
    if mapping == "omitted":
        payload.pop("architecture_components")
    else:
        payload["architecture_components"] = []
    after = save_node(app, planned.id, ProposedMilestone.model_validate(payload), route)
    assert after.milestone(node.id).architecture_components == ["api"]
    assert after.milestone(node.id).model_dump(exclude={"title"}) == before.milestone(
        node.id
    ).model_dump(exclude={"title"})
    assert after.architectures == before.architectures
    assert after.behaviors == before.behaviors
    assert after.evidence == before.evidence
    assert after.targets == before.targets
    assert current_evidence(after, milestone.behavior_revision_ids[0]) == evidence


@pytest.mark.parametrize("route", ["incremental", "full-plan"])
@pytest.mark.parametrize("existing", [False, True], ids=["new", "retained"])
def test_unknown_architecture_references_still_reject_without_mutating_graph(app, route, existing):
    project = app.projects.create("Reference validation")
    seed_architecture(app, project.id)
    node = proposal().milestones[0]
    if existing:
        node.architecture_components = ["api"]
        save_node(app, project.id, node, route, create=True)
    before = app.db.get(project.id)
    node.architecture_components = ["missing"]
    with pytest.raises(ValueError, match="引用的架构组件不存在"):
        save_node(app, project.id, node, route, create=not existing)
    after = app.db.get(project.id)
    assert after.milestones == before.milestones
    assert after.behaviors == before.behaviors
    assert after.architectures == before.architectures
    assert after.evidence == before.evidence


def test_full_plan_cannot_silently_change_retained_execution_mapping(app):
    project = app.projects.create("Stable execution identity")
    seed_architecture(app, project.id)
    node = proposal().milestones[0]
    node.architecture_components = ["api"]
    before = save_node(app, project.id, node, "full-plan", create=True)
    node.architecture_components = ["worker"]
    with pytest.raises(ValueError, match="改变范围、验收或架构关联"):
        save_node(app, project.id, node, "full-plan")
    assert app.db.get(project.id).milestones == before.milestones


@pytest.mark.parametrize("route", ["incremental", "full-plan"])
def test_adding_unmapped_slice_leaves_existing_mapped_delivery_untouched(app, planned, route):
    seed_architecture(app, planned.id)
    existing = proposal().milestones[0]
    existing.architecture_components = ["api"]
    app.graph.upsert(planned.id, existing, create=False)
    before = app.db.get(planned.id)
    added = proposal(
        node="M02", key="exports.csv", statement="CSV export is downloadable"
    ).milestones[0]
    added.scope = ["export.py"]
    if route == "incremental":
        app.graph.upsert(planned.id, added, create=True)
        app.graph.finalize(planned.id)
    else:
        plan = proposal()
        plan.milestones = [existing, added]
        apply_proposal(app, before, plan)
    after = app.db.get(planned.id)
    assert after.milestone("M01") == before.milestone("M01")
    assert not after.milestone("M02").architecture_components
    assert after.milestone("M02").architecture_revision == 1
    assert after.architectures == before.architectures
    assert after.evidence == before.evidence
    assert after.behaviors[: len(before.behaviors)] == before.behaviors
    assert after.targets[: len(before.targets)] == before.targets
