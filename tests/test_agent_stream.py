import asyncio
import json

from conftest import proposal
from evograph.agent_tools import tools
from evograph.domain.models import Model


async def tool_chunks(name, arguments):
    serialized = json.dumps(arguments, ensure_ascii=False)
    yield {"type": "tool_delta", "index": 0, "id": "call_1", "name": name, "arguments": ""}
    for i in range(0, len(serialized), 9):
        yield {
            "type": "tool_delta",
            "index": 0,
            "id": "",
            "name": "",
            "arguments": serialized[i : i + 9],
        }


def test_stream_directly_edits_existing_graph_before_done(app, planned):
    node = proposal().milestones[0].model_dump()
    node["title"] = "Updated title"
    rounds = 0
    provider_finished = False

    async def stream(messages, schemas):
        nonlocal rounds, provider_finished
        assert any(t["function"]["name"] == "update_milestone" for t in schemas)
        rounds += 1
        if rounds == 1:
            async for c in tool_chunks("update_milestone", node):
                yield c
            provider_finished = True
        else:
            yield {"type": "text", "text": "完成"}

    app.settings.stream = stream

    async def collect():
        events = []
        async for event in app.agent.stream(planned.id, "Change the node title"):
            events.append(event)
            if event["type"] == "graph_changed":
                assert not provider_finished, "Graph edit must arrive before provider round ends"
                assert app.db.get(planned.id).milestone("M01").title == "Updated title"
                assert not any(e["type"] == "done" for e in events)
        return events

    events = asyncio.run(collect())
    assert any(e["type"] == "focus" and e["node_id"] == "M01" for e in events)
    assert events[-1]["type"] == "done"
    assert len(app.db.get(planned.id).milestones) == 1
    assert not app.db.get(planned.id).proposal


def test_questions_pause_persist_and_resume_in_same_graph(app, planned):
    rounds = 0

    async def stream(messages, schemas):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            async for event in tool_chunks(
                "ask_user", {"prompt": "使用哪种登录方式？", "options": ["邮箱", "用户名"]}
            ):
                yield event
        elif rounds == 2:
            node = proposal(statement="Email login succeeds").milestones[0].model_dump()
            async for event in tool_chunks("update_milestone", node):
                yield event
        else:
            yield {"type": "text", "text": "完成"}

    app.settings.stream = stream

    async def run():
        first = [e async for e in app.agent.stream(planned.id, "修改登录")]
        question = app.db.get(planned.id).question
        assert question and any(e["type"] == "question" for e in first)
        assert rounds == 1
        assert len(app.db.get(planned.id).behaviors) == 1
        second = [e async for e in app.agent.stream(planned.id, "邮箱", question.id)]
        assert any(e["type"] == "graph_changed" for e in second)
        p = app.db.get(planned.id)
        assert p.question is None
        assert p.behaviors[-1].version == 2
        assert len(p.targets) == 2

    asyncio.run(run())


def test_new_plugin_is_discovered_without_runtime_dispatch_changes(app, planned, monkeypatch):
    from evograph.agent_tools.base import ToolSpec

    class Params(Model):
        value: str

    spec = ToolSpec(
        "custom_test_tool",
        "Test plugin",
        Params,
        lambda ctx, args: {"value": args.value},
        "自定义操作",
        "inspect",
        None,
    )
    monkeypatch.setitem(tools(), spec.name, spec)
    rounds = 0

    async def stream(messages, schemas):
        nonlocal rounds
        assert any(s["function"]["name"] == spec.name for s in schemas)
        rounds += 1
        if rounds == 1:
            async for event in tool_chunks(spec.name, {"value": "plugin works"}):
                yield event
        else:
            # A question is intentional: read-only work does not silently imply a graph edit.
            async for event in tool_chunks("ask_user", {"prompt": "需要继续修改什么？"}):
                yield event

    app.settings.stream = stream

    async def run():
        return [e async for e in app.agent.stream(planned.id, "use plugin")]

    events = asyncio.run(run())
    assert any(e["type"] == "tool_finished" and e["label"] == "自定义操作" for e in events)


def test_invalid_cycle_never_reaches_graph(app, planned):
    async def stream(messages, schemas):
        async for event in tool_chunks(
            "add_dependency", {"source": "M01", "target": "M01", "reason": "bad"}
        ):
            yield event

    app.settings.stream = stream

    async def run():
        return [e async for e in app.agent.stream(planned.id, "invalid request")]

    events = asyncio.run(run())
    assert any(e["type"] == "tool_failed" for e in events)
    assert not app.db.get(planned.id).milestone("M01").dependencies
    assert events[-1]["type"] == "done"


def test_busy_stream_ends_with_terminal_event(app, planned):
    lock = app.operation_lock(planned.id)
    lock.acquire()
    try:

        async def run():
            return [e async for e in app.agent.stream(planned.id, "change")]

        assert asyncio.run(run())[-1]["type"] == "done"
    finally:
        lock.release()
