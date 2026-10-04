"""Existing review rounds expose a read-only forecast of target finalization."""

import asyncio
import json
from copy import deepcopy

import pytest
from conftest import proposal
from test_agent_stream import tool_chunks

PREVIEW_KEYS = {
    "status", "project_revision", "state", "basis", "latest_committed_target_version",
    "statement_changed", "required_behavior_ids_changed", "would_create_target_version",
    "expected_target_version_if_finalized", "committed_required_behavior_count",
    "projected_required_behavior_count",
}


def review_reports(messages, *, include_initial=False):
    reports = []
    for message in messages:
        if message["role"] == "tool":
            report = json.loads(message["content"]).get("result", {})
        elif message["role"] == "system" and "Current review (data):\n" in message["content"]:
            report = json.loads(message["content"].split("Current review (data):\n", 1)[1])
        elif include_initial and message["role"] == "system" and (
            "Current state (data):\n" in message["content"]
        ):
            initial = json.loads(message["content"].split("Current state (data):\n", 1)[1])
            report = initial["design_review"]
        else:
            continue
        if "prospective_target_membership" in report:
            reports.append(report)
    return reports


def assert_compact_preview(report):
    preview = report["target_finalization"]
    assert set(preview) == PREVIEW_KEYS | ({"error"} if preview["status"] == "unavailable" else set())
    assert preview["project_revision"] == report["project_revision"]
    assert preview["state"] == "projection_not_saved"
    assert "not edited again" in preview["basis"]
    assert "finalization succeeds" in preview["basis"]
    assert "not committed" in preview["basis"]
    return preview


def collect_events(app, project_id, content):
    async def collect():
        return [event async for event in app.agent.stream(project_id, content)]

    events = asyncio.run(collect())
    assert not [event for event in events if event["type"] in {"error", "tool_failed"}]
    assert events[-1]["type"] == "done"
    assert events[-1]["summary"]["status"] == "completed"
    return events


@pytest.mark.parametrize("statement_changed,behavior_changed", [
    pytest.param(True, False, id="statement-only"),
    pytest.param(False, True, id="behavior-revision-only"),
    pytest.param(True, True, id="statement-and-behavior"),
    pytest.param(False, False, id="target-unchanged"),
])
@pytest.mark.parametrize("review_order,expected_rounds", [
    ("automatic_only", 3), ("explicit_first", 4), ("automatic_first", 4),
])
def test_provider_sees_target_forecast_before_finalization_without_extra_rounds(
    app, planned, monkeypatch, statement_changed, behavior_changed, review_order, expected_rounds,
):
    committed = planned.targets[-1].model_dump()
    changed = statement_changed or behavior_changed
    expected_version = committed["number"] + int(changed)
    target_statement = "Login with the revised acceptance contract"
    edited = proposal(
        statement="Email login succeeds" if behavior_changed else "Username login succeeds",
    ).milestones[0].model_dump()
    edited["title"] = "Reviewed login delivery"
    calls = []
    if statement_changed:
        calls.append(("set_target", {"statement": target_statement}))
    if behavior_changed or not statement_changed:
        calls.append(("update_milestone", edited))
    captured = []
    reviewed_state = None
    finalized = []
    final_text = f"按当前规划成功收尾后，预计最新目标为 V{expected_version}。"
    narration_saw_forecast = False
    finalize = app.graph.finalize

    def tracked_finalize(project_id):
        assert narration_saw_forecast
        assert len(captured) == expected_rounds
        assert app.db.get(project_id).model_dump() == reviewed_state
        finalized.append(project_id)
        return finalize(project_id)

    monkeypatch.setattr(app.graph, "finalize", tracked_finalize)

    async def stream(messages, schemas):
        nonlocal reviewed_state, narration_saw_forecast
        captured.append(deepcopy(messages))
        round_number = len(captured)
        assert not finalized
        current = app.db.get(planned.id)
        assert [target.model_dump() for target in current.targets] == [committed]
        if round_number == 1:
            initial = assert_compact_preview(review_reports(messages, include_initial=True)[0])
            assert initial["status"] == "unchanged"
            assert initial["expected_target_version_if_finalized"] == committed["number"]
            # Two ordinary tools may share a provider round; forecasting must not
            # add a provider call for either mutation or either review route.
            for index, (name, arguments) in enumerate(calls):
                async for chunk in tool_chunks(name, arguments):
                    yield {
                        **chunk, "index": index,
                        "id": f"call_{index + 1}" if chunk.get("id") else "",
                    }
            return

        if reviewed_state is None:
            reviewed_state = current.model_dump()
        assert current.model_dump() == reviewed_state
        assert current.target_draft == (target_statement if statement_changed else None)
        assert (current.milestone("M01").behavior_revision_ids !=
                committed["required_behavior_ids"]) is behavior_changed
        assert len(current.milestone("M01").behavior_revision_ids) == 1
        for report in review_reports(messages):
            preview = assert_compact_preview(report)
            assert preview["project_revision"] == current.revision
            assert preview["status"] == ("new_version_projected" if changed else "unchanged")
            assert preview["latest_committed_target_version"] == committed["number"]
            assert preview["statement_changed"] is statement_changed
            assert preview["required_behavior_ids_changed"] is behavior_changed
            assert preview["would_create_target_version"] is changed
            assert preview["expected_target_version_if_finalized"] == expected_version
            # Equal counts do not hide a replacement behavior revision.
            assert preview["committed_required_behavior_count"] == 1
            assert preview["projected_required_behavior_count"] == 1

        if (review_order == "explicit_first" and round_number == 2) or (
            review_order == "automatic_first" and round_number == 3
        ):
            async for chunk in tool_chunks("review_design", {}):
                yield chunk
        elif review_reports(messages):
            narration_saw_forecast = True
            yield {"type": "text", "text": final_text}
        else:
            yield {"type": "text", "text": "准备复核当前规划。"}

    app.settings.stream = stream
    events = collect_events(app, planned.id, "Update and review the final target contract")

    assert len(captured) == expected_rounds
    assert finalized == [planned.id]
    reports = review_reports(captured[-1])
    reminder = next(
        message["content"] for message in captured[-1]
        if message["role"] == "system" and "Current review (data):\n" in message["content"]
    )
    assert "Use target_finalization to distinguish statement changes" in reminder
    assert "required behavior revision-ID changes" in reminder
    assert "conditional on the reviewed state remaining unchanged and finalization succeeding" in reminder
    assert "not already committed or guaranteed" in reminder
    assert len(reports) == (1 if review_order == "automatic_only" else 2)
    previews = [assert_compact_preview(report) for report in reports]
    assert all(preview == previews[0] for preview in previews)
    if review_order != "automatic_only":
        assert reports[-1]["change_context"]["status"] == "already_emitted"
        assert reports[-1]["dependency_normalization"]["status"] == "already_emitted"
        assert previews[-1]["status"] != "already_emitted"
    saved = app.db.get(planned.id)
    assert len(saved.targets) == 1 + int(changed)
    assert saved.targets[0].model_dump() == committed
    assert saved.targets[-1].number == expected_version
    assert saved.targets[-1].statement == (
        target_statement if statement_changed else committed["statement"]
    )
    assert saved.targets[-1].required_behavior_ids == saved.milestone("M01").behavior_revision_ids
    assert saved.target_draft is None
    assert app.db.messages(planned.id)[-1]["content"] == final_text
    assert events[-1]["changed"] is True


def test_later_saved_mutation_invalidates_old_target_forecast_on_automatic_review(app, planned):
    committed = planned.targets[-1].model_dump()
    calls = [
        ("set_target", {"statement": "Temporarily revised target"}),
        ("review_design", {}),
        ("set_target", {"statement": committed["statement"]}),
    ]
    captured = []
    revisions = []
    reverted_state = None

    async def stream(messages, schemas):
        nonlocal reverted_state
        captured.append(deepcopy(messages))
        round_number = len(captured)
        current = app.db.get(planned.id)
        assert [target.model_dump() for target in current.targets] == [committed]
        if round_number <= len(calls):
            if round_number == 3:
                preview = assert_compact_preview(review_reports(messages)[-1])
                revisions.append(preview["project_revision"])
                assert preview["status"] == "new_version_projected"
                assert preview["statement_changed"] is True
                assert preview["required_behavior_ids_changed"] is False
                assert preview["would_create_target_version"] is True
                assert preview["expected_target_version_if_finalized"] == committed["number"] + 1
            name, arguments = calls[round_number - 1]
            async for chunk in tool_chunks(name, arguments):
                yield chunk
        else:
            assert current.target_draft == committed["statement"]
            if reverted_state is None:
                reverted_state = current.model_dump()
            assert current.model_dump() == reverted_state
            if round_number == 5:
                old, fresh = [assert_compact_preview(report) for report in review_reports(messages)]
                assert old["project_revision"] == revisions[0] < fresh["project_revision"]
                assert fresh["project_revision"] == current.revision
                assert old["status"] == "new_version_projected"
                assert fresh["status"] == "unchanged"
                assert fresh["statement_changed"] is False
                assert fresh["required_behavior_ids_changed"] is False
                assert fresh["would_create_target_version"] is False
                assert fresh["expected_target_version_if_finalized"] == committed["number"]
                assert fresh["latest_committed_target_version"] == committed["number"]
            yield {"type": "text", "text": "当前目标已恢复原文；成功收尾后预计仍为原版本。"}

    app.settings.stream = stream
    collect_events(app, planned.id, "Review the change, then restore the original target")

    assert len(captured) == 5  # Three tools, narration, existing automatic review.
    saved = app.db.get(planned.id)
    assert [target.model_dump() for target in saved.targets] == [committed]
    assert saved.target_draft is None


def test_repeated_readonly_reviews_keep_complete_compact_target_metadata(app, planned, monkeypatch):
    before = planned.model_dump()
    captured = []

    def unexpected_finalize(project_id):
        pytest.fail("Read-only reviews must not finalize the target")

    monkeypatch.setattr(app.graph, "finalize", unexpected_finalize)

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        assert app.db.get(planned.id).model_dump() == before
        if len(captured) <= 3:
            async for chunk in tool_chunks("review_design", {}):
                yield chunk
        else:
            yield {"type": "text", "text": "已读取当前目标契约。"}

    app.settings.stream = stream
    events = collect_events(app, planned.id, "Review the current target repeatedly")

    assert len(captured) == 4  # No automatic review or forecast-only provider round.
    reports = review_reports(captured[-1], include_initial=True)
    assert len(reports) == 4
    previews = [assert_compact_preview(report) for report in reports]
    assert all(preview == previews[0] for preview in previews)
    assert previews[0]["status"] == "unchanged"
    assert previews[0]["statement_changed"] is False
    assert previews[0]["required_behavior_ids_changed"] is False
    assert previews[0]["would_create_target_version"] is False
    assert previews[0]["expected_target_version_if_finalized"] == planned.targets[-1].number
    assert [report["change_context"]["status"] for report in reports[1:]] == [
        "unchanged", "already_emitted", "already_emitted",
    ]
    assert events[-1]["changed"] is False
    assert app.db.get(planned.id).targets == planned.targets


@pytest.mark.parametrize("state", ["no_target", "unavailable"])
def test_provider_review_reports_absent_or_unavailable_forecast_without_writing(app, planned, state):
    if state == "no_target":
        project = app.projects.create("No target yet")
    else:
        project = app.db.get(planned.id)
        project.milestone("M01").behavior_revision_ids.append("missing_behavior_revision")
        project = app.db.save(project, "test_missing_behavior_reference")
    before = project.model_dump()
    captured = []

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        assert app.db.get(project.id).model_dump() == before
        for report in review_reports(messages, include_initial=True):
            preview = assert_compact_preview(report)
            assert preview["status"] == state
            assert preview["expected_target_version_if_finalized"] is None
            if state == "unavailable":
                assert preview["statement_changed"] is None
                assert preview["required_behavior_ids_changed"] is None
                assert preview["would_create_target_version"] is None
                assert preview["projected_required_behavior_count"] is None
                assert preview["error"]
            else:
                assert preview["latest_committed_target_version"] is None
                assert preview["would_create_target_version"] is False
                assert preview["committed_required_behavior_count"] == 0
                assert preview["projected_required_behavior_count"] == 0
        if len(captured) == 1:
            async for chunk in tool_chunks("review_design", {}):
                yield chunk
        else:
            yield {"type": "text", "text": "已检查当前目标预览状态。"}

    app.settings.stream = stream
    events = collect_events(app, project.id, "Read the current target forecast")

    assert len(captured) == 2
    assert len(review_reports(captured[-1], include_initial=True)) == 2
    assert events[-1]["changed"] is False
    saved = app.db.get(project.id)
    assert saved.targets == project.targets
    assert saved.target_draft == project.target_draft
    assert saved.milestones == project.milestones
