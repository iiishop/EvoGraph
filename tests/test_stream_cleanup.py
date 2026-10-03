"""Resource-lifetime regressions through the real settings and runtime wrappers."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from evograph.application import settings as settings_module


def install_adapter(app, monkeypatch, stream):
    # Keep SettingsService.stream real: replacing it hides its ownership boundary.
    monkeypatch.setattr(
        settings_module, "adapters", lambda: {"fixture": SimpleNamespace(stream=stream)}
    )
    app.db.set_setting("provider", {"adapter": "fixture", "config": {}, "has_key": False})


def tool_chunk(name, arguments):
    return {
        "type": "tool_delta",
        "index": 0,
        "id": "fixture-call",
        "name": name,
        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
    }


def assert_pending(app, project_id, events):
    assert app.operation_lock(project_id).locked(), "Cleanup must retain the project lock"
    result = app.agent.turn_result(project_id, events[0]["turn_id"])
    assert result["pending"] and result["summary"] is None
    assert not any(event["type"] in {"done", "error"} for event in events)


def assert_finished(app, project_id, events, status):
    assert not app.operation_lock(project_id).locked()
    assert project_id not in app.agent.active_turns
    result = app.agent.turn_result(project_id, events[0]["turn_id"])
    assert not result["pending"]
    assert result["summary"]["status"] == status


@pytest.mark.parametrize("exit_kind", ["ask_user", "consumer_error", "provider_error", "exhausted"])
def test_runtime_awaits_adapter_cleanup_before_error_done_or_unlock(
    app, planned, monkeypatch, exit_kind
):
    async def run():
        cleanup_started, finish_cleanup = asyncio.Event(), asyncio.Event()
        timeline, events = [], []

        async def stream(config, secret, messages, tools):
            assert any(tool["function"]["name"] == "ask_user" for tool in tools)
            try:
                yield {"type": "text", "text": "Received narration"}
                if exit_kind == "ask_user":
                    yield tool_chunk("ask_user", {
                        "prompt": "Which operating system should this target?",
                        "category": "decision",
                        "options": ["Linux", "Windows"],
                    })
                elif exit_kind == "consumer_error":
                    yield tool_chunk("ask_user", "x" * 100_001)
                elif exit_kind == "provider_error":
                    raise ValueError("Fixture provider disconnected")
                else:
                    return
                pytest.fail("An early exit must not consume another provider chunk")
            finally:
                timeline.append("cleanup_started")
                cleanup_started.set()
                await finish_cleanup.wait()
                timeline.append("cleanup_finished")

        install_adapter(app, monkeypatch, stream)

        async def consume():
            async for event in app.agent.stream(planned.id, "Investigate the deployment choice"):
                events.append(event)
                if event["type"] in {"error", "done"}:
                    assert timeline == ["cleanup_started", "cleanup_finished"]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(cleanup_started.wait(), 2)
            assert not task.done(), "Runtime must await asynchronous provider cleanup"
            assert timeline == ["cleanup_started"]
            assert_pending(app, planned.id, events)
        finally:
            finish_cleanup.set()
            await task
        status = {"ask_user": "waiting", "exhausted": "completed"}.get(exit_kind, "failed")
        assert_finished(app, planned.id, events, status)
        assert events[-1]["type"] == "done"
        assert events[-1]["summary"]["status"] == status
        errors = [event["message"] for event in events if event["type"] == "error"]
        if exit_kind == "consumer_error":
            assert errors == ["工具参数过长"]
        elif exit_kind == "provider_error":
            assert errors == ["Fixture provider disconnected"]
        else:
            assert errors == []
        assert not any(event["type"] == "tool_failed" for event in events)
        assert any(event["type"] == "question" for event in events) == (exit_kind == "ask_user")
        assert app.db.messages(planned.id)[-1]["content"] == "Received narration"

    asyncio.run(run())


def test_runtime_cancellation_awaits_adapter_cleanup_and_propagates(app, planned, monkeypatch):
    async def run():
        provider_waiting = asyncio.Event()
        cleanup_started, finish_cleanup = asyncio.Event(), asyncio.Event()
        timeline, events = [], []

        async def stream(config, secret, messages, tools):
            try:
                yield {"type": "text", "text": "Partial narration"}
                provider_waiting.set()
                await asyncio.Future()
            finally:
                timeline.append("cleanup_started")
                cleanup_started.set()
                await finish_cleanup.wait()
                timeline.append("cleanup_finished")

        install_adapter(app, monkeypatch, stream)

        async def consume():
            async for event in app.agent.stream(planned.id, "Investigate"):
                events.append(event)

        task = asyncio.create_task(consume())
        await asyncio.wait_for(provider_waiting.wait(), 2)
        task.cancel()
        try:
            await asyncio.wait_for(cleanup_started.wait(), 2)
            assert not task.done()
            assert_pending(app, planned.id, events)
        finally:
            finish_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        # Check in the live loop, before asyncio.run's async-generator shutdown.
        assert timeline == ["cleanup_started", "cleanup_finished"]
        assert_finished(app, planned.id, events, "stopped")
        assert not any(event["type"] in {"done", "error"} for event in events)
        assert app.db.messages(planned.id)[-1]["content"] == "Partial narration"

    asyncio.run(run())


def test_runtime_aclose_awaits_adapter_cleanup_before_unlock(app, planned, monkeypatch):
    async def run():
        cleanup_started, finish_cleanup = asyncio.Event(), asyncio.Event()
        timeline, events = [], []

        async def stream(config, secret, messages, tools):
            try:
                yield {"type": "text", "text": "Narration before close"}
                yield tool_chunk("ask_user", {
                    "prompt": "Which operating system should this target?",
                    "category": "decision",
                    "options": ["Linux", "Windows"],
                })
                pytest.fail("Closed runtime must not resume its provider")
            finally:
                timeline.append("cleanup_started")
                cleanup_started.set()
                await finish_cleanup.wait()
                timeline.append("cleanup_finished")

        install_adapter(app, monkeypatch, stream)
        runtime = app.agent.stream(planned.id, "Investigate")
        async for event in runtime:
            events.append(event)
            if event["type"] == "tool_started":
                break
        task = asyncio.create_task(runtime.aclose())
        try:
            await asyncio.wait_for(cleanup_started.wait(), 2)
            assert not task.done(), "aclose must await both nested generator finalizers"
            assert_pending(app, planned.id, events)
        finally:
            finish_cleanup.set()
            await task
        assert timeline == ["cleanup_started", "cleanup_finished"]
        assert_finished(app, planned.id, events, "stopped")
        assert app.db.get(planned.id).question is None
        assert app.db.messages(planned.id)[-1]["content"] == "Narration before close"

    asyncio.run(run())


@pytest.mark.parametrize("failure", [ValueError("provider failed"), asyncio.CancelledError()])
def test_settings_propagates_original_failure_after_awaited_adapter_cleanup(
    app, monkeypatch, failure
):
    async def run():
        timeline = []

        async def stream(config, secret, messages, tools):
            try:
                yield {"type": "text", "text": "Partial response"}
                raise failure
            finally:
                timeline.append("cleanup_started")
                await asyncio.sleep(0)
                timeline.append("cleanup_finished")

        install_adapter(app, monkeypatch, stream)
        with pytest.raises(type(failure)) as raised:
            async for _ in app.settings.stream([], []):
                pass
        assert raised.value is failure
        assert timeline == ["cleanup_started", "cleanup_finished"]

    asyncio.run(run())
