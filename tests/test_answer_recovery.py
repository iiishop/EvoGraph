"""Authoritative question/lock contracts used by explicit frontend answer recovery."""

import asyncio
import json
from copy import deepcopy

import pytest
from evograph.domain.models import PendingQuestion


def question(project, verification_milestone=None, prompt="导出文件需要哪些字段？"):
    project.question = PendingQuestion(
        prompt=prompt,
        category="decision",
        options=["仅标题", "标题与作者"],
        context="Keep this answer's original scope across a failed provider request",
        verification_milestone=verification_milestone,
    )
    return project.question.model_copy(deep=True)


def tool_chunk(name, arguments):
    return {
        "type": "tool_delta",
        "index": 0,
        "id": "recovery-call",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


@pytest.mark.parametrize("partial_write", [False, True])
@pytest.mark.parametrize("verification", [False, True])
def test_consumed_answer_continues_from_current_state_without_replaying_changes(
    app, planned, partial_write, verification
):
    project = app.db.get(planned.id)
    original = question(project, "M01" if verification else None)
    app.db.save(project, "fixture_question")
    requests = []
    failing = True

    async def provider(messages, schemas):
        requests.append(deepcopy(messages))
        if failing:
            if partial_write and len(requests) == 1:
                yield tool_chunk(
                    "create_milestone",
                    {
                        "id": "M02",
                        "title": "Catalog export",
                        "intent": "Deliver the chosen export fields",
                        "scope": ["exports/catalog.csv"],
                        "behaviors": [
                            {
                                "key": "catalog.export",
                                "statement": "Export contains title and author columns",
                            }
                        ],
                    },
                )
            else:
                raise ValueError("Synthetic provider failure after answer admission")
        else:
            yield tool_chunk(
                "ask_user",
                {
                    "prompt": "是否保留导出中的空字段？",
                    "category": "decision",
                    "options": ["保留", "省略"],
                },
            )

    app.settings.stream = provider

    async def run():
        nonlocal failing
        first = [
            event
            async for event in app.agent.stream(project.id, "标题与作者", question_id=original.id)
        ]
        committed = app.db.get(project.id)
        assert committed.question is None
        assert first[-1]["summary"]["status"] == "failed"
        assert len(committed.milestones) == (2 if partial_write else 1)
        before_messages = len(app.db.messages(project.id))
        failing = False
        # The user explicitly retries from the latest project, carrying the
        # original question context rather than rebinding its expired ID.
        continuation = (
            f"继续处理上次回答。问题：{original.prompt}；回答：标题与作者。保留已保存更改。"
        )
        recovered = [
            event
            async for event in app.agent.stream(
                project.id,
                continuation,
                verification_milestone=original.verification_milestone,
            )
        ]
        after = app.db.get(project.id)
        assert not [event for event in recovered if event["type"] == "error"]
        assert recovered[-1]["summary"]["status"] == "waiting"
        assert after.milestones == committed.milestones
        assert after.behaviors == committed.behaviors
        assert after.question.id != original.id
        assert after.question.verification_milestone == original.verification_milestone
        assert len(app.db.messages(project.id)) == before_messages + 2
        state = json.loads(requests[-1][0]["content"].split("Current state (data):\n", 1)[1])
        assert [node["id"] for node in state["milestones"]] == [
            node.id for node in committed.milestones
        ]
        assert requests[-1][-1] == {"role": "user", "content": continuation}

    asyncio.run(run())


@pytest.mark.parametrize("retry_with_old_id", [False, True])
def test_new_question_between_retry_read_and_send_is_never_consumed(
    app, planned, retry_with_old_id
):
    project = app.db.get(planned.id)
    old = question(project)
    app.db.save(project, "fixture_original_question")
    # A frontend may have just read no question or the old question. A newer
    # question wins regardless of which of those request shapes reaches us.
    current = app.db.get(project.id)
    newer = question(current, prompt="是否允许公开导出链接？")
    app.db.save(current, "fixture_newer_question")
    calls = 0

    async def provider(messages, schemas):
        nonlocal calls
        calls += 1
        yield {"type": "text", "text": "Must not be reached"}

    app.settings.stream = provider

    async def run():
        return [
            event
            async for event in app.agent.stream(
                project.id,
                "标题与作者",
                question_id=old.id if retry_with_old_id else None,
            )
        ]

    events = asyncio.run(run())
    assert calls == 0
    assert app.db.get(project.id).question == newer
    assert not app.db.messages(project.id)
    assert any(event.get("message") == "请先回答图上的待确认问题" for event in events)
    assert events[-1]["summary"]["status"] == "failed"


def test_pre_admission_failure_retains_question_for_a_normal_answer_retry(app, planned):
    project = app.db.get(planned.id)
    original = question(project)
    app.db.save(project, "fixture_question")
    calls = 0

    async def provider(messages, schemas):
        nonlocal calls
        calls += 1
        yield {"type": "text", "text": "Answer accepted"}

    app.settings.stream = provider

    async def run():
        failed = [
            event async for event in app.agent.stream(project.id, " ", question_id=original.id)
        ]
        assert failed[-1]["summary"]["status"] == "failed"
        assert app.db.get(project.id).question == original
        assert calls == 0
        retried = [
            event
            async for event in app.agent.stream(project.id, "标题与作者", question_id=original.id)
        ]
        assert retried[-1]["summary"]["status"] == "completed"
        assert app.db.get(project.id).question is None
        assert calls == 1

    asyncio.run(run())


def test_confirmed_stop_after_answer_admission_preserves_consumed_question(app, planned):
    project = app.db.get(planned.id)
    original = question(project)
    app.db.save(project, "fixture_question")

    async def run():
        entered = asyncio.Event()
        events = []

        async def provider(messages, schemas):
            entered.set()
            await asyncio.Event().wait()
            yield {"type": "text", "text": "Never reached"}

        app.settings.stream = provider

        async def consume():
            async for event in app.agent.stream(project.id, "标题与作者", question_id=original.id):
                events.append(event)

        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        turn_id = next(event["turn_id"] for event in events if event["type"] == "started")
        result = app.agent.turn_result(project.id, turn_id)
        assert result["pending"] is False
        assert result["summary"]["status"] == "stopped"
        assert app.db.get(project.id).question is None
        assert app.operation_lock(project.id).locked() is False

    asyncio.run(run())
