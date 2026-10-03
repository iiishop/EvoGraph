"""Generic direction/receipt checks, not a claim about live model quality."""

import asyncio
import json

import pytest
from conftest import proposal
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.graph import Dependency, RemoveDependency
from evograph.application.tool_execution import ToolExecutor
from evograph.application.turn_summary import build_turn_summary
from evograph.domain.models import ProposedMilestone
from pydantic import ValidationError


@pytest.mark.parametrize("name", ["add_dependency", "remove_dependency"])
def test_schema_names_endpoints_by_their_blocking_role(name):
    spec = tools()[name]
    schema = spec.schema()["function"]["parameters"]
    assert {"prerequisite_id", "dependent_id"} <= set(schema["required"])
    assert not {"source", "target"} & schema["properties"].keys()
    assert "BEFORE" in schema["properties"]["prerequisite_id"]["description"]
    assert "dependencies list" in schema["properties"]["dependent_id"]["description"]
    assert spec.focus_field == "dependent_id"
    assert "B.dependencies" in spec.description
    assert "prerequisites" in ProposedMilestone.model_json_schema()["properties"]["dependencies"]["description"]


@pytest.mark.parametrize("cls", [Dependency, RemoveDependency])
def test_complete_legacy_pair_keeps_original_direction(cls):
    args = {"source": "A", "target": "B"}
    if cls is Dependency:
        args["reason"] = "B needs A"
    parsed = cls.model_validate(args)
    assert parsed.prerequisite_id == "A"
    assert parsed.dependent_id == "B"
    assert not {"source", "target"} & parsed.model_dump().keys()


@pytest.mark.parametrize("endpoints", [
    {"source": "A", "dependent_id": "B"},
    {"prerequisite_id": "A", "target": "B"},
    {"source": "A"},
    {"target": "B"},
    {"source": "A", "target": "B", "prerequisite_id": "A", "dependent_id": "B"},
    {"source": "B", "target": "A", "prerequisite_id": "A", "dependent_id": "B"},
])
@pytest.mark.parametrize("cls", [Dependency, RemoveDependency])
def test_mixed_or_incomplete_endpoint_pairs_are_rejected(cls, endpoints):
    args = {**endpoints, **({"reason": "B needs A"} if cls is Dependency else {})}
    with pytest.raises(ValidationError):
        cls.model_validate(args)


def executor_for(app, planned):
    app.graph.upsert(planned.id, proposal(node="B", key="B").milestones[0], True)
    return ToolExecutor(ToolContext(planned.id, app), tools())


def invoke(executor, name, **args):
    return asyncio.run(executor.invoke(name, json.dumps(args)))


def test_model_receipt_distinguishes_forward_add_reverse_noop_and_real_remove(app, planned):
    executor = executor_for(app, planned)
    added = invoke(executor, "add_dependency", prerequisite_id="M01", dependent_id="B", reason="B needs M01")
    receipt = added["payload"]["result"]["dependency"]
    assert receipt == {
        "prerequisite_id": "M01", "prerequisite_title": "Login",
        "dependent_id": "B", "dependent_title": "Login",
        "edge_existed_before": False, "edge_present_after": True,
        "dependent_prerequisite_ids": ["M01"], "state": "saved_before_transitive_reduction",
    }
    read = invoke(executor, "read_project")["payload"]["result"]
    assert next(m for m in read["milestones"] if m["id"] == "B")["dependencies"] == ["M01"]
    assert app.db.get(planned.id).milestone("M01").dependencies == []

    before_noop = app.db.get(planned.id).model_dump()
    reversed_remove = invoke(executor, "remove_dependency", prerequisite_id="B", dependent_id="M01")
    noop = reversed_remove["payload"]["result"]["dependency"]
    assert noop["edge_existed_before"] is False
    assert noop["edge_present_after"] is False
    assert noop["dependent_prerequisite_ids"] == []
    assert reversed_remove["payload"]["result"]["status"] == "NO_PROGRESS"
    assert reversed_remove["progress"] is False
    assert [e["type"] for e in reversed_remove["events"]] == ["tool_finished"]
    assert app.db.get(planned.id).model_dump() == before_noop
    assert app.db.get(planned.id).milestone("B").dependencies == ["M01"]

    removed = invoke(executor, "remove_dependency", source="M01", target="B")
    receipt = removed["payload"]["result"]["dependency"]
    assert receipt["edge_existed_before"] is True
    assert receipt["edge_present_after"] is False
    assert receipt["dependent_prerequisite_ids"] == []
    assert app.db.get(planned.id).milestone("B").dependencies == []


def test_identical_dependency_does_not_save_or_claim_a_graph_change(app, planned):
    executor = executor_for(app, planned)
    invoke(executor, "add_dependency", prerequisite_id="M01", dependent_id="B", reason="B needs M01")
    before = app.db.get(planned.id).model_dump()
    repeated = invoke(executor, "add_dependency", prerequisite_id="M01", dependent_id="B", reason="B needs M01")
    assert repeated["payload"]["result"]["status"] == "NO_PROGRESS"
    assert repeated["progress"] is False
    assert [e["type"] for e in repeated["events"]] == ["tool_finished"]
    assert app.db.get(planned.id).model_dump() == before
    changed_reason = invoke(executor, "add_dependency", prerequisite_id="M01", dependent_id="B", reason="Needs revised contract")
    assert changed_reason["payload"]["ok"] is True
    assert changed_reason["events"][0]["type"] == "graph_changed"
    assert app.db.get(planned.id).milestone("B").dependency_reasons["M01"] == "Needs revised contract"


def test_mixed_call_does_not_mutate_project(app, planned):
    executor = executor_for(app, planned)
    before = app.db.get(planned.id).model_dump()
    result = invoke(executor, "add_dependency", source="M01", dependent_id="B", reason="B needs M01")
    assert result["payload"]["ok"] is False
    assert app.db.get(planned.id).model_dump() == before


def test_absent_reverse_removal_turn_has_no_graph_change_or_edit_event(app, planned):
    from test_agent_stream import tool_chunks

    executor_for(app, planned)
    app.graph.dependency(planned.id, "M01", "B", "B needs M01")
    app.graph.finalize(planned.id)
    before = app.db.get(planned.id)
    calls = 0

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 1:
            async for event in tool_chunks("remove_dependency", {
                "prerequisite_id": "B", "dependent_id": "M01",
            }):
                yield event
        else:
            yield {"type": "text", "text": "该方向的边原本不存在，未修改。"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(planned.id, "Remove absent reverse edge")]

    events = asyncio.run(collect())
    assert not any(e["type"] == "graph_changed" for e in events)
    assert events[-1]["changed"] is False
    after = app.db.get(planned.id)
    assert after.plans == before.plans
    assert after.milestones == before.milestones
    assert after.targets == before.targets


def test_canonical_stream_focus_receipt_and_final_reduction(app, planned):
    from test_agent_stream import tool_chunks

    executor_for(app, planned)
    app.graph.upsert(planned.id, proposal(node="C", key="C").milestones[0], True)
    app.graph.dependency(planned.id, "M01", "B", "B needs M01")
    app.graph.dependency(planned.id, "B", "C", "C needs B")
    before = app.db.get(planned.id)
    calls = 0
    seen_receipts = []

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 1:
            async for event in tool_chunks("add_dependency", {
                "prerequisite_id": "M01", "dependent_id": "C", "reason": "C needs M01 transitively",
            }):
                yield event
        else:
            seen_receipts.extend(json.loads(m["content"])["result"]["dependency"]
                                 for m in messages if m["role"] == "tool")
            yield {"type": "text", "text": "完成"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(planned.id, "Add prerequisite")]

    events = asyncio.run(collect())
    assert any(e["type"] == "focus" and e["node_id"] == "C" for e in events)
    assert seen_receipts and all(r["edge_present_after"] for r in seen_receipts)
    assert app.db.get(planned.id).milestone("C").dependencies == ["B"]
    summary = build_turn_summary(before, app.db.get(planned.id), "fixture", "completed")
    assert summary["changes"]["dependencies"] == {"added": [], "updated": [], "removed": []}
