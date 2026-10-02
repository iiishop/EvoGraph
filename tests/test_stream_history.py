"""Local fake-provider coverage for negotiated streams and durable history."""

import asyncio
import json
import sqlite3

import pytest
from conftest import proposal
from evograph.application.turn_summary import build_turn_summary
from evograph.infrastructure.database import Database
from evograph.transport.http import AgentRequest, create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def install_edit_provider(app):
    rounds = 0
    narration = "工具轮说明：" + "完整内容#@\n" * 2300

    async def stream(messages, schemas):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield {"type": "text", "text": narration}
            node = proposal().milestones[0].model_dump()
            node["title"] = "Updated from fake provider"
            yield {
                "type": "tool_delta",
                "index": 0,
                "id": "edit",
                "name": "update_milestone",
                "arguments": json.dumps(node),
            }
        else:
            yield {"type": "text", "text": f"完整最终说明 {rounds} " + "续" * 12500}

    app.settings.stream = stream
    return narration


def collect(app, project_id, **params):
    async def run():
        return [event async for event in app.agent.stream(project_id, "请修改", **params)]

    return asyncio.run(run())


@pytest.mark.parametrize("mode", ["full", "compact-v1"])
def test_negotiation_preserves_full_boundaries_and_all_new_narration(app, planned, mode):
    old = app.db.message(planned.id, "assistant", "legacy text stays as saved")
    narration = install_edit_provider(app)
    events = collect(app, planned.id, snapshot_mode=mode)
    assert events[0]["type"] == "started" and events[0]["snapshot_mode"] == mode
    for boundary in [events[0], events[-1]]:
        assert "snapshot_mode" not in boundary["project"]
        assert boundary["project"]["messages"][0] == old
        assert "events" in boundary["project"]
    mutations = [event["project"] for event in events if event["type"] == "graph_changed"]
    assert mutations
    for snapshot in mutations:
        if mode == "compact-v1":
            assert snapshot["snapshot_mode"] == mode
            assert "messages" not in snapshot and "events" not in snapshot
        else:
            assert "messages" in snapshot and "events" in snapshot
    saved = app.db.messages(planned.id)
    assert saved[2]["content"] == narration
    assert all(len(item["content"]) > 12000 for item in saved[2:])
    assert events[-1]["project"]["messages"] == saved
    canonical = [event for event in events if event["type"] == "message_saved"]
    if mode == "compact-v1":
        assert [event["saved_message"] for event in canonical] == saved[2:]
        assert all(event["project_id"] == planned.id for event in canonical)
        assert all(event["turn_id"] == events[0]["turn_id"] for event in canonical)
    else:
        assert not canonical


def test_compact_views_skip_history_queries_and_remove_repeated_payload(app, planned, monkeypatch):
    for _ in range(30):
        app.db.message(planned.id, "assistant", "历史资料" * 1000)
    full = app.projects.get(planned.id)
    monkeypatch.setattr(app.db, "messages", lambda _: pytest.fail("compact queried messages"))
    monkeypatch.setattr(app.db, "events", lambda _: pytest.fail("compact queried events"))
    compact = app.projects.get(planned.id, include_history=False)
    assert {k: v for k, v in full.items() if k not in {"messages", "events"}} == {
        k: v for k, v in compact.items() if k != "snapshot_mode"
    }
    full_bytes = len(json.dumps(full, ensure_ascii=False).encode())
    compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode())
    assert compact_bytes < full_bytes / 10


@pytest.mark.parametrize("interruption", ["provider_error", "cancel"])
def test_interrupted_narration_is_saved_exactly_and_has_honest_outcome(app, planned, interruption):
    text = "已收到但未结束的说明" * 1700

    async def stream(messages, schemas):
        yield {"type": "text", "text": text}
        if interruption == "cancel":
            raise asyncio.CancelledError()
        raise ValueError("Fake provider disconnected")

    app.settings.stream = stream
    events = []

    async def run():
        try:
            async for event in app.agent.stream(planned.id, "调查", snapshot_mode="compact-v1"):
                events.append(event)
        except asyncio.CancelledError:
            assert interruption == "cancel"

    asyncio.run(run())
    assert app.db.messages(planned.id)[-1]["content"] == text
    result = app.agent.turn_result(planned.id, events[0]["turn_id"])
    assert result["summary"]["status"] == ("stopped" if interruption == "cancel" else "failed")
    assert not result["pending"] and not app.operation_lock(planned.id).locked()
    if interruption == "provider_error":
        assert events[-1]["project"]["messages"][-1]["content"] == text


def test_waiting_question_retains_narration_and_provenance(app, planned):
    async def stream(messages, schemas):
        yield {"type": "text", "text": "这一步需要选择路线。"}
        yield {
            "type": "tool_delta",
            "index": 0,
            "id": "question",
            "name": "ask_user",
            "arguments": json.dumps(
                {"prompt": "选择哪条路线？", "category": "decision", "options": ["甲", "乙"]},
                ensure_ascii=False,
            ),
        }

    app.settings.stream = stream
    events = collect(app, planned.id, snapshot_mode="compact-v1")
    question = next(event["question"] for event in events if event["type"] == "question")
    assert events[-1]["project"]["question"] == question
    assert events[-1]["summary"]["status"] == "waiting"
    assert events[-1]["project"]["messages"][-1]["content"] == "这一步需要选择路线。"


def test_exact_old_turn_result_survives_activity_window_and_process_reopen(app, planned):
    old = build_turn_summary(planned, planned, "old-turn", "stopped")
    app.db.save(app.db.get(planned.id), "agent_turn_finished", json.dumps(old))
    for index in range(120):
        app.db.save(app.db.get(planned.id), "activity", str(index))
    assert len(app.db.events(planned.id)) == 100
    assert not any(event["kind"] == "agent_turn_finished" for event in app.db.events(planned.id))
    app.db = Database(app.db.path)
    assert app.agent.turn_result(planned.id, "old-turn")["summary"] == old
    other = app.projects.create("Other")
    assert app.agent.turn_result(other.id, "old-turn")["summary"] is None
    assert app.agent.turn_result(planned.id, "other-turn")["summary"] is None
    with app.db.connect() as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT detail FROM events WHERE project_id=? "
            "AND kind='agent_turn_finished' AND json_extract(CASE WHEN json_valid(detail) "
            "THEN detail ELSE '{}' END, '$.turn_id')=?",
            (planned.id, "old-turn"),
        ).fetchall()
    assert any("events_turn_result" in row[3] for row in plan)


def test_turn_index_migrates_legacy_malformed_events_and_rejects_unknown_versions(app, planned):
    with sqlite3.connect(app.db.path) as db:
        db.execute("DROP INDEX events_turn_result")
    app.db.save(app.db.get(planned.id), "agent_turn_finished", "old non-JSON outcome")
    app.db.save(app.db.get(planned.id), "agent_turn_finished", "{broken")
    future = {**build_turn_summary(planned, planned, "future", "completed"), "version": 2}
    app.db.save(app.db.get(planned.id), "agent_turn_finished", json.dumps(future))
    app.db = Database(app.db.path)
    assert app.agent.turn_result(planned.id, "future")["summary"] is None
    assert app.agent.turn_result(planned.id, "missing")["summary"] is None


def test_http_negotiates_on_original_request_and_unknown_mode_never_admits(app, planned):
    install_edit_provider(app)
    client = TestClient(create_app(app))
    response = client.post(
        "/api/agent/stream",
        json={"project_id": planned.id, "content": "change", "snapshot_mode": "compact-v1"},
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert response.status_code == 200
    assert events[0]["snapshot_mode"] == "compact-v1"
    assert events[-1]["project"]["messages"]
    before = app.db.messages(planned.id)
    assert (
        client.post(
            "/api/agent/stream",
            json={"project_id": planned.id, "content": "change", "snapshot_mode": "future"},
        ).status_code
        == 422
    )
    assert app.db.messages(planned.id) == before
    assert AgentRequest(project_id=planned.id, content="old client").snapshot_mode == "full"
    with pytest.raises(ValidationError):
        AgentRequest(project_id=planned.id, content="bad", snapshot_mode="future")


@pytest.mark.parametrize("interruption", ["aclose", "task_cancel", "none", "provider_error"])
def test_failed_narration_write_preserves_close_and_releases_admitted_turn(
    app, planned, monkeypatch, interruption
):
    original = app.db.message
    blocked = asyncio.Event()

    def fail_assistant(project_id, role, content, composer_document=None):
        if role == "assistant":
            raise OSError("Synthetic assistant storage failure")
        return original(project_id, role, content, composer_document)

    async def stream(messages, schemas):
        yield {"type": "text", "text": "Received narration before storage failed"}
        if interruption == "task_cancel":
            blocked.set()
            await asyncio.Event().wait()
        elif interruption == "aclose":
            yield {
                "type": "tool_delta",
                "index": 0,
                "id": "read",
                "name": "review_design",
                "arguments": "{}",
            }
        elif interruption == "provider_error":
            raise ValueError("Original provider failure")

    monkeypatch.setattr(app.db, "message", fail_assistant)
    app.settings.stream = stream
    events = []

    async def run():
        generator = app.agent.stream(planned.id, "Request", snapshot_mode="compact-v1")
        if interruption == "aclose":
            async for event in generator:
                events.append(event)
                if event["type"] == "tool_started":
                    break
            await generator.aclose()
        else:

            async def consume():
                async for event in generator:
                    events.append(event)

            if interruption == "task_cancel":
                task = asyncio.create_task(consume())
                await blocked.wait()
                task.cancel("original cancellation")
                with pytest.raises(asyncio.CancelledError, match="original cancellation"):
                    await task
            else:
                await consume()

    asyncio.run(run())
    assert not app.operation_lock(planned.id).locked()
    assert planned.id not in app.agent.active_turns
    result = app.agent.turn_result(planned.id, events[0]["turn_id"])
    expected = "stopped" if interruption in {"aclose", "task_cancel"} else "failed"
    assert result["summary"]["status"] == expected
    assert "部分 Agent 回复未能保存" in result["summary"]["history_warning"]
    assert not result["pending"]
    if interruption in {"aclose", "task_cancel"}:
        assert not any(event["type"] in {"error", "done"} for event in events)
    else:
        assert events[-1]["type"] == "done"
        assert events[-1]["summary"] == result["summary"]
        errors = [event["message"] for event in events if event["type"] == "error"]
        assert errors == [
            "Original provider failure"
            if interruption == "provider_error"
            else "Synthetic assistant storage failure"
        ]
