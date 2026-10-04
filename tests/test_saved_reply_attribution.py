"""No-provider runtime witnesses for saved prose and independently saved turn receipts.

Earlier user/assistant prose is an explicit synthetic fixture, not model output.
Messages have no turn linkage; a newer failed receipt must not imply that the
latest saved assistant prose belongs to that failed turn.
"""

import asyncio
import json

import pytest
from evograph.domain.composer import ComposerDocument
from evograph.domain.models import PendingQuestion
from evograph.transport.desktop import DesktopBridge
from evograph.transport.http import create_app
from fastapi.testclient import TestClient


def seed_history(app, project_id):
    app.db.message(project_id, "user", "[Synthetic earlier user fixture] Keep the plan.")
    app.db.message(
        project_id, "assistant", "[Synthetic earlier reply; not model output]\n  保留原文 🙂\n"
    )


def raw_history(app, project_id):
    """Compare durable columns, including the exact stored UTF-8 prose bytes."""
    with app.db.connect() as db:
        return [
            tuple(row)
            for row in db.execute(
                "SELECT m.id, m.project_id, m.role, CAST(m.content AS BLOB), m.created_at, "
                "CAST(d.document AS BLOB) FROM messages m "
                "LEFT JOIN message_documents d ON d.message_id=m.id "
                "WHERE m.project_id=? ORDER BY m.created_at, m.rowid",
                (project_id,),
            )
        ]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


def transport_project(app, project_id):
    desktop = DesktopBridge(app).command("projects.get", {"project_id": project_id})
    response = TestClient(create_app(app)).post(
        "/api/command", json={"action": "projects.get", "params": {"project_id": project_id}}
    )
    assert response.status_code == 200
    assert desktop["ok"] and response.json() == desktop
    return desktop["data"]


def install_sentinels(app, monkeypatch):
    provider_calls, message_writes = [], []
    original_message = app.db.message

    async def provider(*args, **kwargs):
        provider_calls.append(True)
        raise AssertionError("No provider should be reached by this test")
        yield  # Keep the sentinel an async generator.

    def save_message(*args, **kwargs):
        message_writes.append(args[1])
        return original_message(*args, **kwargs)

    def credential_access(*args, **kwargs):
        raise AssertionError("No credentials may be accessed by this test")

    monkeypatch.setattr(app.settings, "stream", provider)
    monkeypatch.setattr(app.db, "message", save_message)
    monkeypatch.setattr(app.settings.secrets, "get", credential_access)
    monkeypatch.setattr(app.settings.secrets, "set", credential_access)
    return provider_calls, message_writes


@pytest.mark.parametrize("mode", ["full", "compact-v1"])
@pytest.mark.parametrize("invalid", ["missing", "foreign"])
@pytest.mark.parametrize("pending_question", [False, True])
def test_prestarted_reference_failure_preserves_old_prose_but_saves_new_failed_receipt(
    app, planned, monkeypatch, mode, invalid, pending_question
):
    # Capture a genuinely current reference before removing it or changing the
    # destination project. No model response or terminal receipt is fabricated.
    item = next(row for row in app.references.catalog(planned.id)["items"] if row["id"] == "M01")
    if invalid == "missing":
        project = planned
        app.graph.remove(project.id, "M01")
        expected_error = "引用对象已不存在或不可用：Login，请重新选择"
    else:
        project = app.projects.create("Synthetic foreign-reference destination")
        expected_error = "引用来自其他项目，请移除或重新选择当前项目的对象"
    document = {
        "version": 1,
        "parts": [
            {"type": "text", "text": "[Synthetic new request] Change "},
            {"type": "reference", **{key: item[key] for key in ("kind", "id", "project_id", "label")}},
        ],
    }
    content = ComposerDocument.model_validate(document).plain_text()
    question_id = None
    if pending_question:
        current = app.db.get(project.id)
        current.question = PendingQuestion(prompt="Synthetic choice?", options=["A", "B"])
        question_id = current.question.id
        app.db.save(current, "synthetic_pending_question")
    seed_history(app, project.id)
    before = transport_project(app, project.id)
    raw_before = raw_history(app, project.id)
    provider_calls, message_writes = install_sentinels(app, monkeypatch)

    async def collect():
        return [
            event
            async for event in app.agent.stream(
                project.id,
                content,
                composer_document=document,
                question_id=question_id,
                snapshot_mode=mode,
            )
        ]

    events = asyncio.run(collect())
    after = transport_project(app, project.id)
    done = events[-1]
    assert events[0] == {"type": "error", "message": expected_error}
    assert [event["type"] for event in events] == ["error", "done"]
    assert provider_calls == message_writes == []
    assert raw_history(app, project.id) == raw_before
    assert encoded(after["messages"]) == encoded(before["messages"])
    assert len(after["messages"]) == 2
    assert all("turn_id" not in message for message in after["messages"])
    assert all(message["content"] != content for message in after["messages"])
    assert after["question"] == before["question"]
    assert done["project"] == after  # Even compact-v1 retains the full terminal snapshot.
    assert not done["changed"] and not done["summary"]["changed"]
    assert done["summary"]["status"] == "failed"
    assert done["summary"]["turn_id"] == done["turn_id"]
    assert after["revision"] == before["revision"] + 1
    assert len(after["events"]) == len(before["events"]) + 1
    receipt = after["events"][0]
    assert receipt["kind"] == "agent_turn_finished"
    assert json.loads(receipt["detail"]) == done["summary"]
    assert receipt["created_at"] >= after["messages"][-1]["created_at"]
    ignored = {"revision", "updated_at", "metrics", "events"}
    assert {k: v for k, v in before.items() if k not in ignored} == {
        k: v for k, v in after.items() if k not in ignored
    }
    assert app.agent.turn_result(project.id, done["turn_id"]) == {
        "turn_id": done["turn_id"], "pending": False, "summary": done["summary"]
    }
    assert app.agent.active_turns == {}
    assert not app.operation_lock(project.id).locked()
    assert app.db.setting("provider") is None


@pytest.mark.parametrize("mode", ["full", "compact-v1"])
def test_valid_request_is_saved_before_started_and_close_keeps_old_prose(app, monkeypatch, mode):
    """Control: stop at the real admission boundary, before any provider call."""
    project = app.projects.create("Synthetic admitted boundary")
    seed_history(app, project.id)
    before = raw_history(app, project.id)
    provider_calls, message_writes = install_sentinels(app, monkeypatch)

    async def admit_then_close():
        stream = app.agent.stream(project.id, "[Synthetic admitted user]", snapshot_mode=mode)
        started = await anext(stream)
        await stream.aclose()
        return started

    started = asyncio.run(admit_then_close())
    assert started["type"] == "started"
    assert started["snapshot_mode"] == mode
    assert started["project"]["messages"][-1]["content"] == "[Synthetic admitted user]"
    assert provider_calls == [] and message_writes == ["user"]
    assert raw_history(app, project.id)[:2] == before
    assert len(app.db.messages(project.id)) == 3
    result = app.agent.turn_result(project.id, started["turn_id"])
    assert result["summary"]["status"] == "stopped" and not result["pending"]
    assert not result["summary"]["changed"]
    assert not app.operation_lock(project.id).locked()


def test_unknown_snapshot_mode_does_not_save_a_turn_or_touch_history(app, monkeypatch):
    project = app.projects.create("Synthetic negotiation boundary")
    seed_history(app, project.id)
    before = transport_project(app, project.id)
    provider_calls, message_writes = install_sentinels(app, monkeypatch)

    async def reject_negotiation():
        return [
            event async for event in app.agent.stream(project.id, "New request", snapshot_mode="future")
        ]

    with pytest.raises(ValueError, match="未知的 Agent 快照格式"):
        asyncio.run(reject_negotiation())
    assert transport_project(app, project.id) == before
    assert provider_calls == message_writes == []
    assert app.agent.active_turns == {}
