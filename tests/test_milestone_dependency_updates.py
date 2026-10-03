"""Incremental dependency presence semantics at the JSON tool boundary."""

import asyncio
import json

import pytest
from conftest import apply_proposal, proposal
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.application.tool_execution import ToolExecutor
from evograph.domain.models import PlanProposal


def node(mid="B", **changes):
    # Build actual tool arguments, not model_dump(): the dependency fields must
    # remain absent when the model is only editing a title or behavior.
    return {
        "id": mid,
        "title": f"Deliver {mid}",
        "intent": f"Provide the {mid} contract",
        "scope": ["auth.py"],
        "behaviors": [{"key": mid, "statement": f"{mid} meets its contract"}],
        **changes,
    }


def invoke(executor, name, args):
    return asyncio.run(executor.invoke(name, json.dumps(args)))


@pytest.fixture
def executor(app, planned):
    executor = ToolExecutor(ToolContext(planned.id, app), tools())
    created = invoke(executor, "create_milestone", node())
    assert created["payload"]["ok"]
    added = invoke(executor, "add_dependency", {
        "prerequisite_id": "M01", "dependent_id": "B",
        "reason": "B consumes the M01 contract", "kind": "migration",
    })
    assert added["payload"]["ok"]
    return executor


@pytest.mark.parametrize("clear", [False, True], ids=["omitted", "explicit-empty"])
def test_omitted_dependencies_preserve_but_explicit_empty_clears(app, planned, executor, clear):
    args = node(title="Updated behavior contract")
    args["behaviors"][0]["statement"] = "The revised B contract is checked"
    if clear:
        args["dependencies"] = []
    else:
        assert "dependencies" not in args
    assert "dependency_reasons" not in args
    result = invoke(executor, "update_milestone", args)
    assert result["payload"]["ok"]
    saved = app.db.get(planned.id).milestone("B")
    assert saved.dependencies == ([] if clear else ["M01"])
    assert saved.dependency_reasons == ({} if clear else {"M01": "B consumes the M01 contract"})
    assert saved.dependency_types == ({} if clear else {"M01": "migration"})
    receipt = result["payload"]["result"]["dependency"]
    assert receipt == {
        "dependent_id": "B",
        "dependent_title": "Updated behavior contract",
        "previous_prerequisite_ids": ["M01"],
        "dependent_prerequisite_ids": saved.dependencies,
        "dependency_reasons": saved.dependency_reasons,
        "dependency_types": saved.dependency_types,
        "state": "saved_before_transitive_reduction",
    }
    assert result["events"][0]["dependency"] == receipt


def test_explicit_reason_change_preserves_omitted_edge_list_and_type(app, planned, executor):
    args = node(dependency_reasons={"M01": "B needs the revised migration contract"})
    assert "dependencies" not in args
    result = invoke(executor, "update_milestone", args)
    assert result["payload"]["ok"]
    saved = app.db.get(planned.id).milestone("B")
    assert saved.dependencies == ["M01"]
    assert saved.dependency_reasons == args["dependency_reasons"]
    assert saved.dependency_types == {"M01": "migration"}


def test_explicit_replacements_keep_surviving_types_and_drop_removed_metadata(app, planned, executor):
    assert invoke(executor, "create_milestone", node("C"))["payload"]["ok"]
    result = invoke(executor, "update_milestone", node(
        dependencies=["M01", "C"],
        dependency_reasons={"M01": "B still needs M01", "C": "B consumes the C contract"},
    ))
    assert result["payload"]["ok"]
    saved = app.db.get(planned.id).milestone("B")
    assert saved.dependencies == ["M01", "C"]
    assert saved.dependency_types == {"M01": "migration", "C": "implementation"}

    # A replacement may omit reasons when it only retains existing edges.
    result = invoke(executor, "update_milestone", node(dependencies=["C"]))
    assert result["payload"]["ok"]
    saved = app.db.get(planned.id).milestone("B")
    assert saved.dependencies == ["C"]
    assert saved.dependency_reasons == {"C": "B consumes the C contract"}
    assert saved.dependency_types == {"C": "implementation"}
    assert result["payload"]["result"]["dependency"]["previous_prerequisite_ids"] == ["M01", "C"]

    # Explicit replacements with a new, complete reason map work too.
    result = invoke(executor, "update_milestone", node(
        dependencies=["M01"], dependency_reasons={"M01": "B now only needs M01"},
    ))
    assert result["payload"]["ok"]
    saved = app.db.get(planned.id).milestone("B")
    assert saved.dependencies == ["M01"]
    assert saved.dependency_reasons == {"M01": "B now only needs M01"}
    # Its former migration edge was removed, so the new edge has the default type.
    assert saved.dependency_types == {"M01": "implementation"}


@pytest.mark.parametrize("changes", [
    {"dependencies": ["M01", "C"]},
    {"dependencies": ["C"], "dependency_reasons": {}},
    {"dependencies": ["M01", "C"], "dependency_reasons": {"C": "Needs C"}},
    {"dependencies": ["M01"], "dependency_reasons": {}},
    {"dependency_reasons": {"M01": "  "}},
    {"dependency_reasons": {"M01": "Needs M01", "C": "Unused reason"}},
    {"dependencies": [], "dependency_reasons": {"M01": "Stale reason"}},
    {"dependencies": ["B"], "dependency_reasons": {"B": "Self cycle"}},
    {"dependencies": ["M01", "M01"]},
    {"dependencies": ["missing"], "dependency_reasons": {"missing": "Unknown prerequisite"}},
    {"dependencies": None},
    {"dependency_reasons": None},
])
def test_invalid_dependency_updates_return_tool_errors_without_mutation(
    app, planned, executor, changes,
):
    assert invoke(executor, "create_milestone", node("C"))["payload"]["ok"]
    before = app.db.get(planned.id).model_dump()
    result = invoke(executor, "update_milestone", node(**changes))
    assert result["payload"]["ok"] is False
    assert result["progress"] is False
    assert [event["type"] for event in result["events"]] == ["tool_failed"]
    assert app.db.get(planned.id).model_dump() == before


def test_cycle_across_existing_nodes_is_rejected_without_mutation(app, planned, executor):
    before = app.db.get(planned.id).model_dump()
    args = node("M01", dependencies=["B"], dependency_reasons={"B": "Would need B first"})
    result = invoke(executor, "update_milestone", args)
    assert result["payload"]["ok"] is False
    assert "环" in result["payload"]["error"]
    assert app.db.get(planned.id).model_dump() == before


def test_behavior_edits_preserve_chain_until_normal_reduction(app, planned, executor):
    graph = {"M1": [], "M2": ["M1"], "M3": ["M1", "M2"], "M4": ["M3"]}
    for mid, dependencies in graph.items():
        result = invoke(executor, "create_milestone", node(
            mid, dependencies=dependencies,
            dependency_reasons={dep: f"{mid} consumes {dep}" for dep in dependencies},
        ))
        assert result["payload"]["ok"]
    for mid, dependencies in graph.items():
        args = node(mid, behaviors=[{"key": mid, "statement": f"Revised check for {mid}"}])
        assert not {"dependencies", "dependency_reasons"} & args.keys()
        result = invoke(executor, "update_milestone", args)
        assert result["payload"]["ok"]
        assert result["payload"]["result"]["dependency"]["dependent_prerequisite_ids"] == dependencies
        assert app.db.get(planned.id).milestone(mid).dependencies == dependencies
    removed = app.graph.finalize(planned.id)
    assert [(edge["source"], edge["target"]) for edge in removed] == [("M1", "M3")]
    saved = app.db.get(planned.id)
    assert {mid: saved.milestone(mid).dependencies for mid in graph} == {
        "M1": [], "M2": ["M1"], "M3": ["M2"], "M4": ["M3"],
    }
    assert saved.milestone("M3").dependency_reasons == {"M2": "M3 consumes M2"}
    assert saved.milestone("M3").dependency_types == {"M2": "implementation"}


def test_source_prerequisites_and_metadata_survive_omitted_update(app, planned, executor):
    from test_baseline_milestones import context, reconstruction

    app.baseline_milestones.save(context(app, planned), reconstruction(planned))
    assert invoke(executor, "add_dependency", {
        "prerequisite_id": "SRC_login", "dependent_id": "B",
        "reason": "B reuses the observed implementation", "kind": "verification",
    })["payload"]["ok"]
    before = app.db.get(planned.id)
    result = invoke(executor, "update_milestone", node(title="Revised B"))
    assert result["payload"]["ok"]
    saved = app.db.get(planned.id)
    for field in ("dependencies", "dependency_reasons", "dependency_types"):
        assert getattr(saved.milestone("B"), field) == getattr(before.milestone("B"), field)
    assert saved.source_milestones == before.source_milestones
    # Clearing a plan node's prerequisites still works and never edits SRC nodes.
    assert invoke(executor, "update_milestone", node(dependencies=[]))["payload"]["ok"]
    saved = app.db.get(planned.id)
    assert saved.milestone("B").dependencies == []
    assert saved.milestone("B").dependency_reasons == {}
    assert saved.milestone("B").dependency_types == {}
    assert saved.source_milestones == before.source_milestones


@pytest.mark.parametrize("reason", [None, "", "  "])
def test_source_replacements_require_a_reason_without_mutation(app, planned, executor, reason):
    from test_baseline_milestones import context, reconstruction

    app.baseline_milestones.save(context(app, planned), reconstruction(planned))
    before = app.db.get(planned.id).model_dump()
    changes = {"dependencies": ["SRC_login"]}
    if reason is not None:
        changes["dependency_reasons"] = {"SRC_login": reason}
    result = invoke(executor, "update_milestone", node(**changes))
    assert result["payload"]["ok"] is False
    assert "理由" in result["payload"]["error"]
    assert app.db.get(planned.id).model_dump() == before


def test_create_omission_means_no_dependencies_and_explicit_list_still_works(app, planned, executor):
    assert invoke(executor, "create_milestone", node("C"))["payload"]["ok"]
    assert app.db.get(planned.id).milestone("C").dependencies == []
    result = invoke(executor, "create_milestone", node(
        "D", dependencies=["C"], dependency_reasons={"C": "D consumes C"},
    ))
    assert result["payload"]["ok"]
    saved = app.db.get(planned.id).milestone("D")
    assert saved.dependencies == ["C"]
    assert saved.dependency_reasons == {"C": "D consumes C"}
    assert result["payload"]["result"]["dependency"]["dependent_prerequisite_ids"] == ["C"]
    before = app.db.get(planned.id).model_dump()
    result = invoke(executor, "create_milestone", node("E", dependencies=["C"]))
    assert result["payload"]["ok"] is False
    assert "理由" in result["payload"]["error"]
    assert app.db.get(planned.id).model_dump() == before


def test_full_plan_dependencies_remain_explicit_not_incremental(app, planned, executor):
    # A full-plan omission still means [], not a patch that inherits old edges.
    plan = PlanProposal.model_validate({
        "target": "Full replacement", "summary": "Keep existing nodes",
        "milestones": [proposal().milestones[0].model_dump(), node()],
    })
    assert plan.milestones[1].dependencies == []
    before = app.db.get(planned.id)
    with pytest.raises(ValueError, match="已有执行身份"):
        apply_proposal(app, planned, plan)
    assert app.db.get(planned.id).milestones == before.milestones

    fresh = app.projects.create("Full-plan fixture")
    plan = PlanProposal.model_validate({
        "target": "Complete plan", "summary": "Fresh full plan",
        "milestones": [node("X"), node("Y", dependencies=["X"], dependency_reasons={"X": "Uses X"})],
    })
    saved = apply_proposal(app, fresh, plan)
    assert saved.milestone("X").dependencies == []
    assert saved.milestone("Y").dependencies == ["X"]


def test_tool_schema_describes_omission_replacement_and_saved_receipt():
    spec = tools()["update_milestone"]
    schema = spec.schema()["function"]["parameters"]
    assert "dependencies" not in schema["required"]
    assert "dependency_reasons" not in schema["required"]
    assert "[] to clear" in schema["properties"]["dependencies"]["description"]
    assert "On create and full-plan proposals, omission means none" in schema["properties"]["dependencies"]["description"]
    assert "replaces all reasons" in schema["properties"]["dependency_reasons"]["description"]
    assert "saved prerequisites" in spec.description
