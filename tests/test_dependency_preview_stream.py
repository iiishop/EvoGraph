"""Canonical preview reaches normal narration without changing finalizer timing."""

import asyncio
import json
from copy import deepcopy

import pytest
from conftest import proposal
from test_agent_stream import tool_chunks


def normalization_previews(messages):
    previews = []
    for message in messages:
        if message["role"] == "tool":
            report = json.loads(message["content"]).get("result", {})
        elif message["role"] == "system" and "Current review (data):\n" in message["content"]:
            report = json.loads(message["content"].split("Current review (data):\n", 1)[1])
        else:
            continue
        if "dependency_normalization" in report:
            previews.append(report["dependency_normalization"])
    return previews


def chain(app, project_id):
    for mid in ("B", "C"):
        app.graph.upsert(project_id, proposal(node=mid, key=mid).milestones[0], True)
    app.graph.dependency(project_id, "M01", "B", "B consumes M01", "migration")
    app.graph.dependency(project_id, "B", "C", "C consumes B", "verification")
    app.graph.finalize(project_id)


@pytest.mark.parametrize("explicit_review,expected_rounds", [(False, 3), (True, 4)])
def test_narration_sees_projected_direct_edges_before_unchanged_finalizer(
    app, planned, explicit_review, expected_rounds,
):
    chain(app, planned.id)
    captured = []
    final_text = "C 的最终直接前置预计为 B；M01 仍通过 B 构成前置约束。"

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        count = len(captured)
        if count == 1:
            async for chunk in tool_chunks("add_dependency", {
                "prerequisite_id": "M01", "dependent_id": "C",
                "reason": "C also consumes M01 transitively", "kind": "implementation",
            }):
                yield chunk
        elif explicit_review and count == 2:
            async for chunk in tool_chunks("review_design", {}):
                yield chunk
        else:
            # Both review routes remain read-only. Reduction still happens only
            # after model completion, and the mutation receipt stays immediate.
            assert app.db.get(planned.id).milestone("C").dependencies == ["B", "M01"]
            previews = normalization_previews(messages)
            detailed = [p for p in previews if p["status"] != "already_emitted"]
            if detailed:
                preview = detailed[-1]
                assert preview["state"] == "projection_not_saved"
                assert preview["dependents"] == [{
                    "dependent_id": "C",
                    "current_direct_prerequisite_ids": ["B", "M01"],
                    "projected_direct_prerequisite_ids": ["B"],
                    "removed_direct_prerequisite_ids": ["M01"],
                }]
                yield {"type": "text", "text": final_text}
            else:
                yield {"type": "text", "text": "准备复核当前规划。"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(planned.id, "Review the final direct graph")]

    events = asyncio.run(collect())
    assert not [event for event in events if event["type"] in {"error", "tool_failed"}]
    assert len(captured) == expected_rounds
    previews = normalization_previews(captured[-1])
    assert sum(p["status"] == "reduction_projected" for p in previews) == 1
    if explicit_review:
        assert previews[-1] == {"status": "already_emitted",
                                "project_revision": previews[0]["project_revision"]}
    receipt = next(event["dependency"] for event in events if "dependency" in event)
    assert receipt["dependent_prerequisite_ids"] == ["B", "M01"]
    assert receipt["state"] == "saved_before_transitive_reduction"
    saved = app.db.get(planned.id)
    assert saved.milestone("C").dependencies == ["B"]
    assert saved.milestone("C").dependency_types == {"B": "verification"}
    assert saved.milestone("C").dependency_reasons == {"B": "C consumes B"}
    assert app.db.messages(planned.id)[-1]["content"] == final_text
    assert any("自动移除 1 条" in event.get("message", "") for event in events)


def test_later_edit_invalidates_preview_without_an_extra_review_round(app, planned):
    chain(app, planned.id)
    calls = [
        ("add_dependency", {"prerequisite_id": "M01", "dependent_id": "C", "reason": "Shortcut"}),
        ("review_design", {}),
        ("remove_dependency", {"prerequisite_id": "B", "dependent_id": "C"}),
    ]
    captured = []

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        if len(captured) <= len(calls):
            name, args = calls[len(captured) - 1]
            async for chunk in tool_chunks(name, args):
                yield chunk
        else:
            yield {"type": "text", "text": "已按最新规划状态复核。"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(planned.id, "Revise after review")]

    events = asyncio.run(collect())
    assert events[-1]["summary"]["status"] == "completed"
    assert len(captured) == 5
    previews = normalization_previews(captured[-1])
    assert [p["status"] for p in previews] == ["reduction_projected", "already_canonical"]
    assert previews[0]["project_revision"] < previews[1]["project_revision"]
    assert "not edited again" in previews[0]["basis"]
    assert app.db.get(planned.id).milestone("C").dependencies == ["M01"]


def test_review_description_does_not_present_projected_mutation_as_saved():
    from evograph.agent_tools import tools

    description = tools()["review_design"].description
    assert "projected final direct edges" in description
    assert "retained prerequisite reachability" in description
    assert "conditional, not already saved" in description
