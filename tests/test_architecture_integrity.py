"""Architecture evolution invariants; no live provider or semantic PASS claims."""

import asyncio
from copy import deepcopy

import pytest
from conftest import proposal
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.domain.design_review import review_design
from evograph.domain.models import ArchitectureSpec, MigrationStep, ResearchSource


def architecture(**updates):
    payload = {
        "summary": "Local API architecture",
        "technologies": [{"area": "API", "choice": "Python", "rationale": "Existing stack"}],
        "diagram": {
            "id": "system",
            "title": "System",
            "nodes": [{"id": "api", "label": "API", "description": "Own the API contract"}],
        },
    }
    return ArchitectureSpec.model_validate({**payload, **updates})


def retire_api(app, project_id):
    app.design.update(project_id, architecture())
    spec = architecture()
    spec.diagram.nodes[0].id = "next_api"
    spec.retirements = [
        MigrationStep(component_id="api", milestone_id="M01", instruction="Migrate then retire")
    ]
    app.design.update(project_id, spec)
    return app.db.get(project_id)


@pytest.mark.parametrize("field", ["milestone_ids", "attachment_ids"])
def test_architecture_rejects_unknown_diagram_references_without_saving(app, planned, field):
    before = app.db.get(planned.id).model_dump()
    spec = architecture()
    setattr(spec.diagram, field, ["missing"])
    with pytest.raises(ValueError, match="不存在"):
        app.design.update(planned.id, spec)
    assert app.db.get(planned.id).model_dump() == before


@pytest.mark.parametrize("operation", ["architecture", "diagram"])
def test_duplicate_group_members_are_rejected_without_saving(app, planned, operation):
    spec = architecture()
    data = spec.model_dump()
    data["diagram"]["groups"] = [
        {"id": "server", "label": "Server", "member_node_ids": ["api", "api"]}
    ]
    spec = ArchitectureSpec.model_validate(data)
    before = app.db.get(planned.id).model_dump()
    with pytest.raises(ValueError, match="不能重复"):
        if operation == "architecture":
            app.design.update(planned.id, spec)
        else:
            app.design.save_diagram(planned.id, spec.diagram)
    assert app.db.get(planned.id).model_dump() == before


@pytest.mark.parametrize("newer_revision", [False, True])
@pytest.mark.parametrize("route", ["incremental", "full_plan"])
def test_removed_retirement_owner_is_reported_across_revisions(app, planned, route, newer_revision):
    p = retire_api(app, planned.id)
    if newer_revision:
        spec = architecture(summary="Refine next API")
        spec.diagram.nodes[0].id = "next_api"
        app.design.update(p.id, spec)
        p = app.db.get(p.id)
        assert not p.architectures[-1].retirements
    if route == "full_plan":
        p.proposal = proposal(node="M02", key="other.behavior", statement="Other behavior")
        p.proposal_revision = p.revision + 1
        p = app.db.save(p, "test_proposal")
    if route == "incremental":
        app.graph.remove(p.id, "M01")
    else:
        app.planning.apply(p.id, p.revision)
    after = app.db.get(p.id)
    assert "M01" not in {m.id for m in after.milestones}
    assert after.architectures == p.architectures
    missing = next(
        f for f in review_design(after)["findings"] if f["code"] == "retirement_owner_missing"
    )
    assert missing["severity"] == "review"
    assert "A2" in missing["message"]


@pytest.mark.parametrize("route", ["incremental", "full_plan"])
def test_completed_retirement_owner_can_be_removed_as_history(app, planned, route):
    p = retire_api(app, planned.id)
    # Fixture status only; no acceptance result is fabricated by the application.
    p.milestone("M01").status = "VERIFIED_COMPLETE"
    if route == "full_plan":
        p.proposal = proposal(node="M02", key="other.behavior", statement="Other behavior")
        p.proposal_revision = p.revision + 1
    p = app.db.save(p, "test_completed_retirement")
    if route == "incremental":
        app.graph.remove(p.id, "M01")
    else:
        app.planning.apply(p.id, p.revision)
    after = app.db.get(p.id)
    assert "M01" not in {m.id for m in after.milestones}
    assert after.architectures == p.architectures
    missing = next(
        f for f in review_design(after)["findings"] if f["code"] == "retirement_owner_missing"
    )
    assert missing["severity"] == "review"


def test_full_plan_retains_existing_migration_steps(app, planned):
    p = retire_api(app, planned.id)
    steps = deepcopy(p.milestone("M01").migration_steps)
    p.proposal = proposal()
    p.proposal_revision = p.revision + 1
    p = app.db.save(p, "test_proposal")
    after = app.planning.apply(p.id, p.revision)
    assert after.milestone("M01").migration_steps == steps


def test_review_reports_corrupt_migration_ownership_without_mutating(app, planned):
    p = retire_api(app, planned.id)
    p.milestone("M01").migration_steps[0].milestone_id = "missing"
    before = p.model_dump()
    findings = {f["code"]: f for f in review_design(p)["findings"]}
    assert findings["retirement_owner_mismatch"]["severity"] == "error"
    assert findings["retirement_step_missing"]["severity"] == "error"
    assert p.model_dump() == before


def test_review_reports_missing_stored_retirement_step(app, planned):
    p = retire_api(app, planned.id)
    p.milestone("M01").migration_steps = []
    before = p.model_dump()
    missing = next(
        f for f in review_design(p)["findings"] if f["code"] == "retirement_step_missing"
    )
    assert missing["severity"] == "error"
    assert p.model_dump() == before


def test_abandoned_migration_does_not_trap_unstarted_work(app, planned):
    p = retire_api(app, planned.id)
    restored = architecture(summary="Abandon the planned replacement; retain the original API")
    restored.diagram.nodes.append(
        restored.diagram.nodes[0].model_copy(update={"id": "next_api", "label": "Next API"})
    )
    app.design.update(p.id, restored)
    before = app.db.get(p.id)
    assert before.milestone("M01").status == "PLANNED"
    app.graph.remove(p.id, "M01")
    after = app.db.get(p.id)
    assert after.architectures == before.architectures
    findings = review_design(after)["findings"]
    assert any(f["code"] == "retirement_owner_missing" for f in findings)
    assert not any(f["severity"] == "error" for f in findings)


def seed_rationale(app, project_id):
    p = app.db.get(project_id)
    source = ResearchSource(
        title="Supplied reference", url="https://example.com", excerpt="x", query="x"
    )
    p.research.append(source)
    app.db.save(p, "test_research")
    app.design.update(
        project_id,
        architecture(
            decisions=["Keep the existing boundary"],
            quality_scenarios=[
                {
                    "concern": "Reliability",
                    "scenario": "Restart",
                    "measure": "No lost records",
                    "approach": "Transactions",
                }
            ],
            risks=["Concurrency remains unmeasured"],
            research_ids=[source.id],
        ),
    )
    return app.db.get(project_id)


@pytest.mark.parametrize("route", ["service", "api", "tool"])
def test_omitted_rationale_survives_architecture_update(app, planned, route):
    before = seed_rationale(app, planned.id)
    spec = architecture(summary="Refine API overview")
    original_input = spec.model_dump()
    if route == "service":
        app.design.update(planned.id, spec)
    elif route == "api":
        result = asyncio.run(
            app.dispatch(
                "design.update",
                {
                    "project_id": planned.id,
                    "specification": spec.model_dump(exclude_unset=True),
                },
            )
        )
        assert result["ok"]
    else:
        tool = tools()["update_architecture"]
        args = tool.parameters.model_validate_json(spec.model_dump_json(exclude_unset=True))
        tool.handler(ToolContext(planned.id, app), args)
    after = app.db.get(planned.id)
    assert after.architectures[:-1] == before.architectures
    assert len(after.architectures) == len(before.architectures) + 1
    assert after.architectures[-1].summary == spec.summary
    for field in ("decisions", "quality_scenarios", "risks", "research_ids"):
        assert getattr(after.architectures[-1], field) == getattr(before.architectures[-1], field)
    assert spec.model_dump() == original_input


def test_explicit_empty_rationale_clears_without_rewriting_history(app, planned):
    before = seed_rationale(app, planned.id)
    app.design.update(
        planned.id, architecture(decisions=[], quality_scenarios=[], risks=[], research_ids=[])
    )
    after = app.db.get(planned.id)
    assert after.architectures[:-1] == before.architectures
    for field in ("decisions", "quality_scenarios", "risks", "research_ids"):
        assert getattr(after.architectures[-1], field) == []


def test_omitted_rationale_is_merged_before_no_progress_check(app, planned):
    before = seed_rationale(app, planned.id)
    result = app.design.update(planned.id, architecture())
    assert result["status"] == "NO_PROGRESS"
    assert app.db.get(planned.id).model_dump() == before.model_dump()


def test_retirements_are_not_implicitly_carried_into_next_revision(app, planned):
    before = retire_api(app, planned.id)
    spec = architecture(summary="Refine next API overview")
    spec.diagram.nodes[0].id = "next_api"
    app.design.update(planned.id, spec)
    after = app.db.get(planned.id)
    assert not after.architectures[-1].retirements
    assert after.architectures[:-1] == before.architectures
    assert after.milestone("M01").migration_steps == before.milestone("M01").migration_steps


def test_omitting_previous_retirement_delta_does_not_create_a_revision(app, planned):
    before = retire_api(app, planned.id)
    spec = architecture()
    spec.diagram.nodes[0].id = "next_api"
    result = app.design.update(planned.id, spec)
    assert result["status"] == "NO_PROGRESS"
    assert app.db.get(planned.id).model_dump() == before.model_dump()


def test_unchanged_architecture_still_rejects_unrelated_retirement_delta(app, planned):
    before = retire_api(app, planned.id)
    spec = architecture()
    spec.diagram.nodes[0].id = "next_api"
    spec.retirements = [
        MigrationStep(component_id="unknown", milestone_id="M01", instruction="Invalid removal")
    ]
    with pytest.raises(ValueError, match="retirements"):
        app.design.update(planned.id, spec)
    assert app.db.get(planned.id).model_dump() == before.model_dump()
