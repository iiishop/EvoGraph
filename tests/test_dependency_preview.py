"""Read-only canonical dependency projections agree with the actual finalizer."""

import json
import random

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.design_review import current_review
from evograph.domain.dependencies import (
    MAX_PREVIEW_BYTES,
    MAX_PREVIEW_DEPENDENTS,
    ancestor_sets,
    dependency_reduction_preview,
    reduce_dependencies,
)
from evograph.domain.models import BehaviorRevision, Milestone, Project, SourceBehavior


def nodes(graph, kind="implementation"):
    return [
        Milestone(
            id=mid, title=f"Deliver {mid}", intent=f"Provide the {mid} contract",
            scope=[f"src/{mid}.py"], dependencies=list(prerequisites),
            dependency_reasons={dep: f"{mid} consumes {dep}" for dep in prerequisites},
            dependency_types={dep: kind for dep in prerequisites},
            position={"x": index * 10.0, "y": 25.0},
        )
        for index, (mid, prerequisites) in enumerate(graph.items())
    ]


def serialized(result):
    return json.dumps(result, ensure_ascii=False).encode("utf-8")


def row_map(result):
    return {row["dependent_id"]: row for row in result["dependents"]}


def assert_bounded(result):
    assert result["limits"] == {"bytes": 8192, "dependents": 32}
    assert MAX_PREVIEW_BYTES == 8192
    assert MAX_PREVIEW_DEPENDENTS == 32
    assert len(serialized(result)) <= 8192
    assert len(result["dependents"]) <= 32


def assert_whole_rows(result, original, canonical, removed):
    current = {node.id: node.dependencies for node in original}
    projected = {node.id: node.dependencies for node in canonical}
    by_target = {}
    for edge in removed:
        by_target.setdefault(edge["target"], []).append(edge["source"])
    for row in result["dependents"]:
        mid = row["dependent_id"]
        assert row == {
            "dependent_id": mid,
            "current_direct_prerequisite_ids": current[mid],
            "projected_direct_prerequisite_ids": projected[mid],
            "removed_direct_prerequisite_ids": by_target[mid],
        }
    assert len(row_map(result)) == len(result["dependents"])
    assert result["removed_shortcut_count"] == len(removed)
    assert result["affected_dependent_count"] == len(by_target)
    assert result["omitted_dependent_count"] == len(by_target) - len(result["dependents"])
    assert result["truncated"] is (len(result["dependents"]) < len(by_target))
    assert_bounded(result)


def test_deep_source_chain_reports_reducer_capacity_without_mutating_inputs():
    # Source observations do not share the 256 planned-node limit. The existing
    # recursive finalizer's limit must not make a read-only review fail outright.
    original = nodes({f"SRC_{i}": [f"SRC_{i - 1}"] if i else [] for i in range(1200)})
    for node in original:
        node.origin = "source"
        node.source_baseline_id = "source-baseline"
    original.reverse()
    before = [node.model_dump() for node in original]

    result = dependency_reduction_preview(original, 9)

    assert result["status"] == "unavailable"
    assert result["removed_shortcut_count"] is None
    assert result["affected_dependent_count"] is None
    assert result["omitted_dependent_count"] is None
    assert result["dependents"] == []
    assert "recursion" in result["error"].lower()
    assert [node.model_dump() for node in original] == before
    assert_bounded(result)


@pytest.mark.parametrize("graph", [
    {}, {"a": []}, {"a": [], "b": ["a"]},
    {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"], "isolated": []},
])
def test_already_canonical_graph_reports_exact_zero_counts_without_mutating(graph):
    original = nodes(graph)
    before = [node.model_dump() for node in original]

    result = dependency_reduction_preview(original, 17)

    assert result["status"] == "already_canonical"
    assert result["state"] == "projection_not_saved"
    assert result["project_revision"] == 17
    assert result["dependents"] == []
    assert_whole_rows(result, original, original, [])
    assert [node.model_dump() for node in original] == before


@pytest.mark.parametrize("kind", ["implementation", "migration", "verification"])
def test_exact_projection_preserves_dependency_direction_order_and_edge_kinds(kind):
    original = nodes({
        "root": [], "left": ["root"], "right": ["root"],
        "terminal": ["root", "right", "left"],
        "consumer": ["root", "right", "left", "terminal"], "isolated": [],
    }, kind)
    before = [node.model_dump() for node in original]

    result = dependency_reduction_preview(original, 23)

    assert result["status"] == "reduction_projected"
    assert result["state"] == "projection_not_saved"
    assert result["project_revision"] == 23
    assert result["removed_shortcut_count"] == 4
    assert result["affected_dependent_count"] == 2
    assert result["dependents"] == [
        {
            "dependent_id": "consumer",
            "current_direct_prerequisite_ids": ["root", "right", "left", "terminal"],
            "projected_direct_prerequisite_ids": ["terminal"],
            "removed_direct_prerequisite_ids": ["root", "right", "left"],
        },
        {
            "dependent_id": "terminal",
            "current_direct_prerequisite_ids": ["root", "right", "left"],
            "projected_direct_prerequisite_ids": ["right", "left"],
            "removed_direct_prerequisite_ids": ["root"],
        },
    ]
    assert result["omitted_dependent_count"] == 0
    assert result["truncated"] is False
    assert [node.model_dump() for node in original] == before
    assert_bounded(result)


def test_seeded_dags_project_the_exact_existing_algorithm_without_changing_reachability():
    rng = random.Random(302)
    for size in range(2, 16):
        graph = {
            f"n{index:02}": [f"n{dep:02}" for dep in range(index) if rng.random() < 0.6]
            for index in range(size)
        }
        original = nodes(graph)
        before = [node.model_dump() for node in original]
        canonical = [node.model_copy(deep=True) for node in original]
        removed = reduce_dependencies(canonical)

        result = dependency_reduction_preview(iter(original), size)

        assert_whole_rows(result, original, canonical, removed)
        assert result["omitted_dependent_count"] == 0
        assert result["status"] == ("reduction_projected" if removed else "already_canonical")
        assert ancestor_sets({node.id: node.dependencies for node in canonical}) == ancestor_sets(graph)
        assert result == dependency_reduction_preview(reversed(original), size)
        assert [node.model_dump() for node in original] == before


@pytest.mark.parametrize("invalid", [
    {"invalid": ["missing"]},
    {"invalid": ["invalid"]},
    {"cycle_a": ["cycle_b"], "cycle_b": ["cycle_a"]},
    {"invalid": ["不存在的完整节点🧪" * 600]},
], ids=["missing", "self-cycle", "cycle", "long-missing-id"])
def test_invalid_graph_has_unknown_counts_and_no_partial_projection_or_mutation(invalid):
    original = nodes({"a": [], "b": ["a"], "c": ["a", "b"], **invalid})
    before = [node.model_dump() for node in original]

    result = dependency_reduction_preview(original, 5)

    assert result["status"] == "unavailable"
    assert result["state"] == "projection_not_saved"
    assert result["project_revision"] == 5
    assert result["error"]
    for count in ("removed_shortcut_count", "affected_dependent_count", "omitted_dependent_count"):
        assert result[count] is None
    assert result["dependents"] == []
    assert result["truncated"] is False
    assert [node.model_dump() for node in original] == before
    assert_bounded(result)


def test_dependent_limit_counts_omitted_whole_rows_deterministically():
    original = nodes({"r": [], "b": ["r"], **{f"d{i:02}": ["r", "b"] for i in range(40)}})
    canonical = [node.model_copy(deep=True) for node in original]
    removed = reduce_dependencies(canonical)

    result = dependency_reduction_preview(original, 8)

    assert len(result["dependents"]) == 32
    assert list(row_map(result)) == [f"d{i:02}" for i in range(32)]
    assert result["omitted_dependent_count"] == 8
    assert_whole_rows(result, original, canonical, removed)
    assert result == dependency_reduction_preview(reversed(original), 8)


def test_byte_budget_is_utf8_and_omits_whole_unicode_ids_without_shortening():
    graph = {"r": [], "b": ["r"]}
    graph.update({f"d{i:02}-" + "界🧪" * 60: ["r", "b"] for i in range(24)})
    original = nodes(graph)
    canonical = [node.model_copy(deep=True) for node in original]
    removed = reduce_dependencies(canonical)

    result = dependency_reduction_preview(original, 11)

    assert 0 < len(result["dependents"]) < 24 < 32
    assert result["truncated"] is True
    assert_whole_rows(result, original, canonical, removed)
    assert result == dependency_reduction_preview(reversed(original), 11)


@pytest.mark.parametrize("oversized", ["dependent", "prerequisite"])
def test_oversized_row_is_omitted_and_later_complete_rows_still_fit(oversized):
    huge = "a-complete-id-" + "整行标识🧪" * 3000
    large_id = huge if oversized == "dependent" else "a-dependent"
    large_prerequisite = huge if oversized == "prerequisite" else "a-root"
    original = nodes({
        large_prerequisite: [], "a-bridge": [large_prerequisite],
        large_id: [large_prerequisite, "a-bridge"],
        "z-root": [], "z-bridge": ["z-root"], "z-dependent": ["z-root", "z-bridge"],
    })
    canonical = [node.model_copy(deep=True) for node in original]
    removed = reduce_dependencies(canonical)

    result = dependency_reduction_preview(original, 12)

    assert list(row_map(result)) == ["z-dependent"]
    assert result["omitted_dependent_count"] == 1
    assert "a-complete-id-" not in serialized(result).decode("utf-8")
    assert_whole_rows(result, original, canonical, removed)


@pytest.fixture
def mixed_project():
    sources = nodes({"SRC_base": [], "SRC_api": ["SRC_base"]}, "verification")
    for node in sources:
        node.origin = "source"
        node.status = "IMPLEMENTED"
        node.source_baseline_id = "observed-baseline"
        node.source_refs = [f"{node.id}.py:1-10"]
        node.source_behaviors = [SourceBehavior(
            key=f"{node.id}.observed", statement=f"Observed {node.id} behavior",
            source_refs=node.source_refs[:],
        )]
    planned = nodes({
        "delivery": ["SRC_base", "SRC_api"],
        "consumer": ["SRC_base", "delivery", "SRC_api"],
    }, "migration")
    behaviors = []
    for node in planned:
        for scope in ("target", "milestone"):
            behavior = BehaviorRevision(
                id=f"{node.id}-{scope}", behavior_key=f"{node.id}.{scope}", version=1,
                owner=node.id, statement=f"Exact {scope} requirement", acceptance_scope=scope,
            )
            behaviors.append(behavior)
            node.behavior_revision_ids.append(behavior.id)
    return Project(
        id="dependency-preview-fixture", name="Dependency preview", revision=31,
        source_milestones=sources, milestones=planned, behaviors=behaviors,
    )


def test_review_includes_source_paths_and_preserves_all_project_data(mixed_project):
    project = mixed_project
    before = project.model_dump()
    ctx = ToolContext(project.id, None, before_snapshot=project.model_copy(deep=True))

    report = current_review(ctx, project)
    result = report["dependency_normalization"]

    assert row_map(result)["delivery"]["current_direct_prerequisite_ids"] == ["SRC_base", "SRC_api"]
    assert row_map(result)["delivery"]["projected_direct_prerequisite_ids"] == ["SRC_api"]
    assert row_map(result)["consumer"]["projected_direct_prerequisite_ids"] == ["delivery"]
    assert result["removed_shortcut_count"] == 3
    assert result["state"] == "projection_not_saved"
    assert report["change_context"]["project_revision"] == project.revision
    assert report["prospective_target_membership"]["target_behavior_count"] == 2
    assert project.model_dump() == before
    assert ctx.before_snapshot.model_dump() == before

    # A caller editing a returned list must not be able to edit the saved graph.
    row_map(result)["delivery"]["current_direct_prerequisite_ids"].append("not-saved")
    row_map(result)["consumer"]["projected_direct_prerequisite_ids"].clear()
    assert project.model_dump() == before


def test_review_deduplicates_per_revision_and_recomputes_after_repair(mixed_project):
    project = mixed_project
    ctx = ToolContext(project.id, None, before_snapshot=project.model_copy(deep=True))
    first = current_review(ctx, project)
    snapshot = project.model_dump()

    repeated = current_review(ctx, project)

    assert first["dependency_normalization"]["status"] == "reduction_projected"
    for name in ("dependency_normalization", "change_context"):
        assert repeated[name] == {"status": "already_emitted", "project_revision": project.revision}
    assert project.model_dump() == snapshot
    reduce_dependencies([*project.source_milestones, *project.milestones])
    project.revision += 1
    repaired_snapshot = project.model_dump()

    repaired = current_review(ctx, project)

    assert repaired["dependency_normalization"]["status"] == "already_canonical"
    assert repaired["dependency_normalization"]["project_revision"] == project.revision
    assert repaired["change_context"]["status"] != "already_emitted"
    assert ctx.review_context_revision == project.revision
    assert project.model_dump() == repaired_snapshot


def test_projection_matches_actual_finalization_and_review_never_saves(app, mixed_project):
    saved = app.projects.create("Canonical dependency fixture")
    saved.source_milestones = mixed_project.source_milestones
    saved.milestones = mixed_project.milestones
    saved.behaviors = mixed_project.behaviors
    app.db.save(saved, "fixture_graph")
    before = app.db.get(saved.id)
    before_dump = before.model_dump()
    before_events = app.db.events(saved.id)
    ctx = ToolContext(saved.id, app, before_snapshot=before.model_copy(deep=True))

    report = current_review(ctx)
    result = report["dependency_normalization"]

    assert app.db.get(saved.id).model_dump() == before_dump
    assert app.db.events(saved.id) == before_events
    removed = app.graph.finalize(saved.id)
    after = app.db.get(saved.id)
    original = [*before.source_milestones, *before.milestones]
    canonical = [*after.source_milestones, *after.milestones]
    assert_whole_rows(result, original, canonical, removed)
    assert result["project_revision"] == before.revision < after.revision
    assert after.behaviors == before.behaviors
    assert after.source_milestones == before.source_milestones
    for node in after.milestones:
        old = before.milestone(node.id)
        assert node.scope == old.scope
        assert node.behavior_revision_ids == old.behavior_revision_ids
        assert node.dependency_reasons == {dep: old.dependency_reasons[dep] for dep in node.dependencies}
        assert node.dependency_types == {dep: old.dependency_types[dep] for dep in node.dependencies}
    assert ancestor_sets({node.id: node.dependencies for node in canonical}) == ancestor_sets(
        {node.id: node.dependencies for node in original}
    )
    assert current_review(ctx)["dependency_normalization"]["status"] == "already_canonical"
    assert app.graph.finalize(saved.id) == []
    assert app.db.get(saved.id).model_dump() == after.model_dump()
