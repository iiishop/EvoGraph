"""Existing provider cadence gets factual review context, not another review loop."""

import asyncio
import json
from copy import deepcopy

import pytest
from conftest import proposal
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.design_review import current_review
from test_agent_stream import tool_chunks


def contexts(messages):
    found = []
    for message in messages:
        if message["role"] == "tool":
            review = json.loads(message["content"])["result"]
        elif message["role"] == "system" and "Current review (data):\n" in message["content"]:
            review = json.loads(message["content"].split("Current review (data):\n", 1)[1])
        else:
            continue
        if "change_context" in review:
            found.append(review["change_context"])
    return found


@pytest.mark.parametrize("review_order,expected_rounds", [
    ("automatic_only", 3), ("explicit_first", 4), ("automatic_first", 4),
])
def test_existing_reviews_share_one_context_per_revision_without_extra_rounds(
    app, planned, review_order, expected_rounds,
):
    before = app.db.get(planned.id)
    edited = proposal().milestones[0].model_dump()
    edited["title"] = "Updated contract owner"
    captured = []

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        round_number = len(captured)
        if round_number == 1:
            async for chunk in tool_chunks("update_milestone", edited):
                yield chunk
        elif (review_order == "explicit_first" and round_number == 2) or (
            review_order == "automatic_first" and round_number == 3
        ):
            async for chunk in tool_chunks("review_design", {}):
                yield chunk
        else:
            yield {"type": "text", "text": "已保存规划；未执行实现或验收。"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(planned.id, "Update the milestone")]

    events = asyncio.run(collect())
    assert not [event for event in events if event["type"] in {"error", "tool_failed"}]
    assert len(captured) == expected_rounds
    emitted = contexts(captured[-1])
    detailed = [context for context in emitted if context["status"] == "changed"]
    assert len(detailed) == 1
    context = detailed[0]
    assert context["before_revision"] == before.revision
    node = next(node for node in context["nodes"] if node["id"] == "M01")
    assert node["before_overrides"]["title"] == before.milestone("M01").title
    assert node["current"]["title"] == edited["title"]
    assert all("change_context" not in event for event in events if event["type"] == "graph_changed")
    if review_order != "automatic_only":
        assert [item["status"] for item in emitted] == ["changed", "already_emitted"]
        assert emitted[-1]["project_revision"] == context["project_revision"]
        assert set(emitted[-1]) == {"status", "project_revision"}


def test_repair_after_explicit_review_gets_fresh_context_on_existing_next_review(app, planned):
    first = proposal(statement="First revised behavior").milestones[0].model_dump()
    repaired = proposal(statement="Repaired observable behavior").milestones[0].model_dump()
    calls = [("update_milestone", first), ("review_design", {}),
             ("update_milestone", repaired), ("review_design", {})]
    captured = []

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        if len(captured) <= len(calls):
            name, args = calls[len(captured) - 1]
            async for chunk in tool_chunks(name, args):
                yield chunk
        else:
            yield {"type": "text", "text": "规划已保存"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(planned.id, "Revise and review")]

    events = asyncio.run(collect())
    assert events[-1]["summary"]["status"] == "completed"
    assert len(captured) == 6  # Four tools, completion, existing automatic-review round.
    emitted = contexts(captured[-1])
    assert [item["status"] for item in emitted] == ["changed", "changed", "already_emitted"]
    assert emitted[0]["project_revision"] < emitted[1]["project_revision"]
    assert emitted[1]["project_revision"] == emitted[2]["project_revision"]
    contract = next(row for row in emitted[1]["contracts"] if row["node_id"] == "M01")
    assert contract["current"]["statement"] == "Repaired observable behavior"
    assert contract["before"]["statement"] == "Username login succeeds"


def test_review_with_no_baseline_or_changes_is_readonly_and_does_not_persist_context(app, planned):
    saved = app.db.get(planned.id)
    before = saved.model_dump()
    ctx = ToolContext(planned.id, app)
    assert current_review(ctx)["change_context"]["status"] == "no_turn_start_snapshot"
    ctx = ToolContext(planned.id, app, before_snapshot=saved.model_copy(deep=True))
    review = current_review(ctx)
    assert review["change_context"]["status"] == "unchanged"
    assert review["change_context"]["nodes"] == []
    assert current_review(ctx)["change_context"]["status"] == "already_emitted"
    assert app.db.get(planned.id).model_dump() == before
    assert ctx.before_snapshot.model_dump() == before


def test_readonly_stream_has_no_automatic_review_or_extra_provider_round(app, planned):
    captured = []

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        if len(captured) == 1:
            async for chunk in tool_chunks("review_design", {}):
                yield chunk
        else:
            yield {"type": "text", "text": "已读取当前契约"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(planned.id, "Review current state")]

    events = asyncio.run(collect())
    assert len(captured) == 2
    assert events[-1]["changed"] is False
    assert [item["status"] for item in contexts(captured[-1])] == ["unchanged"]
