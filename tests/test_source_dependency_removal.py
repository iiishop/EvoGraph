"""Plan references to read-only SRC capabilities have a symmetric tool lifecycle."""

import asyncio
import json

import pytest
from conftest import accept_external, proposal
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.application.tool_execution import ToolExecutor
from test_baseline_milestones import context, reconstruction


def invoke(executor, name, **args):
    return asyncio.run(executor.invoke(name, json.dumps(args)))


@pytest.fixture
def executor(app, planned):
    app.baseline_milestones.save(
        context(app, planned),
        reconstruction(planned, {"SRC_base": [], "SRC_login": ["SRC_base"]}),
    )
    executor = ToolExecutor(ToolContext(planned.id, app), tools())
    assert invoke(
        executor, "create_milestone", **proposal(node="B", key="B").milestones[0].model_dump()
    )["payload"]["ok"]
    app.execution.positions(
        planned.id,
        {
            "SRC_base": {"x": 10, "y": 20},
            "SRC_login": {"x": 30, "y": 40},
        },
    )
    return executor


def add(executor, **changes):
    return invoke(
        executor,
        "add_dependency",
        **{
            "prerequisite_id": "SRC_login",
            "dependent_id": "B",
            "reason": "B reuses the observed login implementation",
            "kind": "verification",
            **changes,
        },
    )


def events_by_id(app, pid):
    return {event["id"]: event for event in app.db.events(pid)}


def assert_rejected_unchanged(app, pid, executor, name, args, error):
    before = app.db.get(pid).model_dump()
    events = events_by_id(app, pid)
    result = invoke(executor, name, **args)
    assert result["payload"]["ok"] is False
    assert error in result["payload"]["error"]
    assert result["progress"] is False
    assert [event["type"] for event in result["events"]] == ["tool_failed"]
    assert app.db.get(pid).model_dump() == before
    assert events_by_id(app, pid) == events


@pytest.mark.parametrize("legacy", [False, True], ids=["canonical", "legacy"])
@pytest.mark.parametrize("kind", ["implementation", "migration", "verification"])
def test_add_remove_cleans_only_plan_reference_and_preserves_history(
    app,
    planned,
    executor,
    legacy,
    kind,
):
    # Include actual accepted evidence, prior plans and targets, and a source
    # node with its own prerequisite, observed behaviors, refs and saved layout.
    app.execution.start(planned.id, "M01")
    accept_external(app, planned.id, "M01")
    assert add(executor, prerequisite_id="M01", reason="B consumes M01", kind="migration")[
        "payload"
    ]["ok"]
    app.graph.finalize(planned.id)
    initial = app.db.get(planned.id)
    assert initial.evidence and initial.acceptance_requests and initial.targets
    endpoints = (
        {"source": "SRC_login", "target": "B"}
        if legacy
        else {
            "prerequisite_id": "SRC_login",
            "dependent_id": "B",
        }
    )
    added = invoke(executor, "add_dependency", **endpoints, reason="Reuse login", kind=kind)
    assert added["payload"]["ok"] is True
    before = app.db.get(planned.id)
    assert before.milestone("B").dependency_types["SRC_login"] == kind
    history = events_by_id(app, planned.id)

    removed = invoke(executor, "remove_dependency", **endpoints)
    assert removed["payload"]["ok"] is True
    assert removed["progress"] is True
    assert [event["type"] for event in removed["events"]] == ["graph_changed"]
    receipt = removed["payload"]["result"]["dependency"]
    assert receipt == {
        "prerequisite_id": "SRC_login",
        "prerequisite_title": "登录调用占位能力 SRC_login",
        "dependent_id": "B",
        "dependent_title": "Login",
        "edge_existed_before": True,
        "edge_present_after": False,
        "dependent_prerequisite_ids": ["M01"],
        "state": "saved_before_transitive_reduction",
    }
    assert removed["events"][0]["node_ids"] == ["B"]
    assert removed["events"][0]["dependency"] == receipt
    after = app.db.get(planned.id)
    dependent = after.milestone("B")
    assert dependent.dependencies == ["M01"]
    assert dependent.dependency_reasons == {"M01": "B consumes M01"}
    assert dependent.dependency_types == {"M01": "migration"}
    assert after.revision == before.revision + 1
    assert len(after.plans) == len(before.plans) + 1
    assert after.plans[:-1] == before.plans
    assert app.graph.finalize(planned.id) == []
    after = app.db.get(planned.id)
    assert after.milestone("M01") == initial.milestone("M01")
    for field in [name for name in type(initial).model_fields if name.startswith("source_")]:
        assert getattr(after, field) == getattr(initial, field)
    for field in ("behaviors", "baselines", "evidence", "acceptance_requests", "targets"):
        assert getattr(after, field) == getattr(initial, field)
    assert history.items() <= events_by_id(app, planned.id).items()


@pytest.mark.parametrize("remove_existing_first", [False, True], ids=["absent", "repeated"])
def test_absent_source_reference_is_a_truthful_noop(app, planned, executor, remove_existing_first):
    if remove_existing_first:
        assert add(executor)["payload"]["ok"]
        assert invoke(executor, "remove_dependency", prerequisite_id="SRC_login", dependent_id="B")[
            "payload"
        ]["ok"]
    # A fresh executor must not mark the no-op as a mutation because of setup.
    executor = ToolExecutor(ToolContext(planned.id, app), tools())
    before = app.db.get(planned.id).model_dump()
    history = events_by_id(app, planned.id)
    result = invoke(executor, "remove_dependency", prerequisite_id="SRC_login", dependent_id="B")
    assert result["payload"]["ok"] is True
    assert result["payload"]["result"]["status"] == "NO_PROGRESS"
    receipt = result["payload"]["result"]["dependency"]
    assert receipt["edge_existed_before"] is False
    assert receipt["edge_present_after"] is False
    assert receipt["dependent_prerequisite_ids"] == []
    assert not result["progress"] and not executor.changed
    assert [event["type"] for event in result["events"]] == ["tool_finished"]
    assert app.db.get(planned.id).model_dump() == before
    assert events_by_id(app, planned.id) == history


@pytest.mark.parametrize("present", [False, True], ids=["absent", "present"])
@pytest.mark.parametrize("name", ["add_dependency", "remove_dependency"])
def test_leased_plan_dependent_rejects_source_reference_edits(
    app, planned, executor, present, name
):
    if present:
        assert add(executor)["payload"]["ok"]
    app.execution.resolve_obligation(planned.id, "B", "scope", True, "Reviewed auth.py")
    app.execution.start(planned.id, "B")
    assert_rejected_unchanged(
        app,
        planned.id,
        executor,
        name,
        {
            "prerequisite_id": "SRC_login",
            "dependent_id": "B",
            **({"reason": "Reuse login"} if name == "add_dependency" else {}),
        },
        "正在执行",
    )


@pytest.mark.parametrize("present", [False, True], ids=["absent", "present"])
@pytest.mark.parametrize("name", ["add_dependency", "remove_dependency"])
def test_archived_project_rejects_source_reference_edits(app, planned, executor, present, name):
    if present:
        assert add(executor)["payload"]["ok"]
    app.projects.delete(planned.id)
    assert_rejected_unchanged(
        app,
        planned.id,
        executor,
        name,
        {
            "prerequisite_id": "SRC_login",
            "dependent_id": "B",
            **({"reason": "Reuse login"} if name == "add_dependency" else {}),
        },
        "项目已删除",
    )


@pytest.mark.parametrize(
    "source,target",
    [
        ("SRC_base", "SRC_login"),  # Existing source-to-source edge remains immutable.
        ("M01", "SRC_login"),
        ("SRC_login", "SRC_login"),
        ("SRC_missing", "B"),
        ("missing", "B"),
        ("SRC_login", "missing"),
        ("SRC_login", "SRC_missing"),
    ],
)
@pytest.mark.parametrize("name", ["add_dependency", "remove_dependency"])
def test_source_targets_and_unknown_endpoints_remain_invalid(
    app,
    planned,
    executor,
    source,
    target,
    name,
):
    assert_rejected_unchanged(
        app,
        planned.id,
        executor,
        name,
        {
            "prerequisite_id": source,
            "dependent_id": target,
            **({"reason": "Requires prerequisite"} if name == "add_dependency" else {}),
        },
        "里程碑不存在",
    )


def test_absent_source_removal_turn_emits_no_graph_change(app, planned, executor):
    from test_agent_stream import tool_chunks

    app.graph.finalize(planned.id)
    before = app.db.get(planned.id)
    calls = 0

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 1:
            async for event in tool_chunks(
                "remove_dependency",
                {
                    "prerequisite_id": "SRC_login",
                    "dependent_id": "B",
                },
            ):
                yield event
        else:
            yield {"type": "text", "text": "该依赖原本不存在，未修改。"}

    app.settings.stream = stream

    async def collect():
        return [
            event async for event in app.agent.stream(planned.id, "Remove absent source reference")
        ]

    events = asyncio.run(collect())
    assert not any(event["type"] in {"graph_changed", "tool_failed", "error"} for event in events)
    assert events[-1]["type"] == "done" and events[-1]["changed"] is False
    after = app.db.get(planned.id)
    for field in ("plans", "milestones", "source_milestones", "targets", "behaviors", "evidence"):
        assert getattr(after, field) == getattr(before, field)
