"""Generation budgets must not become a lifetime roadmap ceiling."""

import pytest
from evograph.domain.models import PlanProposal, ProposedMilestone
from evograph.domain.policies import MAX_WORKING_MILESTONES
from pydantic import ValidationError


def node(i, dependencies=()):
    return ProposedMilestone(
        id=f"M{i:03}",
        title=f"Delivery {i}",
        intent="A reviewable PR-sized increment",
        scope=[f"feature/{i}.py"],
        dependencies=list(dependencies),
        dependency_reasons={dep: "Uses the preceding contract" for dep in dependencies},
        behaviors=[{"key": f"feature.{i}", "statement": "The contract holds"}],
    )


def grow(app, count):
    project = app.projects.create("Evolving roadmap")
    for i in range(count):
        app.graph.upsert(project.id, node(i), create=True)
    return project.id


def test_incremental_growth_past_generation_budget_remains_editable(app):
    pid = grow(app, 32)
    app.graph.upsert(pid, node(24).model_copy(update={"title": "Refined scope"}), create=False)
    app.graph.dependency(pid, "M000", "M031", "Integrates the initial contract")
    app.graph.finalize(pid)
    project = app.db.get(pid)
    assert len(project.milestones) == 32
    assert project.milestone("M024").title == "Refined scope"
    assert project.milestone("M031").dependencies == ["M000"]
    assert len(project.targets[-1].required_behavior_ids) == 32
    app.graph.dependency(pid, "M000", "M031", remove=True)
    app.graph.remove(pid, "M031")
    assert len(app.db.get(pid).milestones) == 31


def test_large_graph_rejects_cycles_unknown_dependencies_and_duplicate_behavior(app):
    pid = grow(app, 25)
    app.graph.dependency(pid, "M000", "M024", "Requires the first contract")
    before = app.db.get(pid).model_dump()
    with pytest.raises(ValueError, match="环"):
        app.graph.dependency(pid, "M024", "M000", "Invalid loop")
    with pytest.raises(ValueError, match="不存在"):
        app.graph.upsert(pid, node(25, ["missing"]), create=True)
    duplicate = node(25).model_copy(update={"behaviors": node(0).behaviors})
    with pytest.raises(ValueError, match="已归属"):
        app.graph.upsert(pid, duplicate, create=True)
    assert app.db.get(pid).model_dump() == before


def test_generation_budget_stays_small():
    with pytest.raises(ValidationError):
        PlanProposal(
            target="Target", summary="One generation", milestones=[node(i) for i in range(25)]
        )
    assert (
        len(
            PlanProposal(
                target="Target", summary="One generation", milestones=[node(i) for i in range(24)]
            ).milestones
        )
        == 24
    )


def test_persistent_capacity_is_bounded_and_overflow_does_not_mutate(app):
    # Seed in one save to avoid turning a boundary test into quadratic disk writes.
    from evograph.domain.models import BehaviorRevision, Milestone

    p = app.projects.create("Capacity boundary")
    for i in range(MAX_WORKING_MILESTONES):
        proposed = node(i, [f"M{i - 1:03}"] if i else [])
        behavior = BehaviorRevision(
            behavior_key=f"feature.{i}",
            statement="The contract holds",
            owner=proposed.id,
            version=1,
        )
        p.behaviors.append(behavior)
        p.milestones.append(
            Milestone(
                **proposed.model_dump(exclude={"behaviors"}), behavior_revision_ids=[behavior.id]
            )
        )
    app.graph._check(p)
    app.db.save(p, "capacity_fixture")
    before = app.db.get(p.id).model_dump()
    with pytest.raises(ValueError, match="最多支持 256"):
        app.graph.upsert(p.id, node(MAX_WORKING_MILESTONES), create=True)
    assert app.db.get(p.id).model_dump() == before
    app.graph.upsert(
        p.id, node(0).model_copy(update={"title": "Still editable at capacity"}), create=False
    )
    app.graph.finalize(p.id)
    assert len(app.db.get(p.id).targets[-1].required_behavior_ids) == MAX_WORKING_MILESTONES
