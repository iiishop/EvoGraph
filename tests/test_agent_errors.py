"""Synthetic failures only: no provider, network, or credential access."""

import asyncio
import json

import httpx
import pytest
from conftest import proposal
from evograph.agent_tools import tools
from evograph.application.agent_errors import agent_error_event

SECRET = "private-error-canary-do-not-expose"

FAILURES = [
    (httpx.ConnectTimeout, "httpx_connect_timeout"),
    (httpx.ReadTimeout, "httpx_read_timeout"),
    (httpx.WriteTimeout, "httpx_write_timeout"),
    (httpx.PoolTimeout, "httpx_pool_timeout"),
    (httpx.TimeoutException, "httpx_timeout"),
    (httpx.ConnectError, "httpx_connect_error"),
    (httpx.ReadError, "httpx_read_error"),
    (httpx.WriteError, "httpx_write_error"),
    (httpx.CloseError, "httpx_close_error"),
    (httpx.NetworkError, "httpx_network_error"),
    (httpx.RemoteProtocolError, "httpx_remote_protocol_error"),
    (httpx.LocalProtocolError, "httpx_local_protocol_error"),
    (httpx.ProtocolError, "httpx_protocol_error"),
    (httpx.ProxyError, "httpx_proxy_error"),
    (httpx.UnsupportedProtocol, "httpx_unsupported_protocol"),
    (httpx.TransportError, "httpx_transport_error"),
    (httpx.DecodingError, "httpx_decoding_error"),
    (httpx.TooManyRedirects, "httpx_too_many_redirects"),
    (httpx.RequestError, "httpx_request_error"),
    (httpx.InvalidURL, "httpx_invalid_url"),
    (httpx.StreamError, "httpx_stream_error"),
    (httpx.HTTPError, "httpx_http_error"),
    (TypeError, "internal_type_error"),
    (KeyError, "internal_key_error"),
    (IndexError, "internal_index_error"),
    (AttributeError, "internal_attribute_error"),
    (AssertionError, "internal_assertion_error"),
    (OSError, "internal_io_error"),
    (RuntimeError, "internal_runtime_error"),
    (Exception, "internal_error"),
    (type(SECRET, (Exception,), {}), "internal_error"),
]


@pytest.mark.parametrize("error_type,code", FAILURES)
def test_network_and_unexpected_errors_expose_only_fixed_diagnostics(error_type, code):
    error = error_type(SECRET)
    error.__cause__ = RuntimeError(SECRET)
    event = agent_error_event(error)
    assert event["type"] == "error" and event["code"] == code
    assert code in event["message"]  # Visible with the unchanged UI error handler.
    assert SECRET not in json.dumps(event)
    assert "工具调用" not in event["message"]
    assert event == agent_error_event(error_type("different private contents"))


def test_http_status_error_does_not_expose_request_response_or_exception_text():
    request = httpx.Request(
        "POST",
        f"https://{SECRET}.invalid/{SECRET}",
        headers={"Authorization": SECRET},
        content=SECRET,
    )
    response = httpx.Response(402, request=request, headers={"X-Secret": SECRET}, text=SECRET)
    event = agent_error_event(httpx.HTTPStatusError(SECRET, request=request, response=response))
    assert event["code"] == "httpx_http_status_error"
    assert "HTTP 402" in event["message"]
    assert SECRET not in json.dumps(event)


def test_exception_stringification_is_not_used_for_unexpected_failures():
    class PrivateFailure(RuntimeError):
        def __str__(self):
            pytest.fail("Unexpected failure details were read")

        def __repr__(self):
            pytest.fail("Unexpected failure details were read")

    assert agent_error_event(PrivateFailure())["code"] == "internal_runtime_error"


@pytest.mark.parametrize(
    "message",
    [
        "Provider 流式请求失败（HTTP 402）",
        "请先在设置中配置支持工具调用的 Provider",
        "工具参数过长",
    ],
)
def test_existing_application_value_error_contract_is_intentionally_preserved(message):
    # These are application-authored messages, not sanitized network exceptions.
    assert agent_error_event(ValueError(message)) == {"type": "error", "message": message}


def collect(app, project_id, mode):
    async def run():
        return [
            event async for event in app.agent.stream(project_id, "修改计划", snapshot_mode=mode)
        ]

    return asyncio.run(run())


@pytest.mark.parametrize("mode", ["full", "compact-v1"])
@pytest.mark.parametrize(
    "error_type,code", FAILURES[:4] + [FAILURES[10], FAILURES[22], FAILURES[23]]
)
def test_failure_before_provider_output_releases_turn_without_retry(
    app, planned, mode, error_type, code, caplog
):
    calls = 0

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        raise error_type(SECRET)
        yield  # Keep the fake provider an async generator.

    app.settings.stream = stream
    events = collect(app, planned.id, mode)
    assert calls == 1
    assert [event for event in events if event["type"] == "error"] == [
        agent_error_event(error_type(SECRET))
    ]
    done = events[-1]
    assert done["type"] == "done" and not done["changed"]
    assert done["summary"]["status"] == "failed"
    assert not app.operation_lock(planned.id).locked()
    assert not app.agent.turn_result(planned.id, done["turn_id"])["pending"]
    assert SECRET not in json.dumps(events) + json.dumps(app.db.events(planned.id)) + caplog.text


@pytest.mark.parametrize("mode", ["full", "compact-v1"])
@pytest.mark.parametrize("failure_source", ["provider", "tool"])
@pytest.mark.parametrize("finalize_fails", [False, True])
def test_failure_retains_narration_saved_edits_drafts_and_durable_outcome(
    app, planned, monkeypatch, mode, failure_source, finalize_fails, caplog
):
    node = proposal().milestones[0].model_dump()
    node["title"] = "Saved before interruption"
    narration = "已经收到的说明，不能丢弃。"
    target = "Saved target statement"
    calls, closed = 0, False

    def fail_tool(context, args):
        raise TypeError(SECRET)

    if failure_source == "tool":
        monkeypatch.setattr(tools()["read_project"], "handler", fail_tool)
    if finalize_fails:

        def fail_finalize(project_id):
            raise RuntimeError(SECRET)

        monkeypatch.setattr(app.graph, "finalize", fail_finalize)

    async def stream(messages, schemas):
        nonlocal calls, closed
        calls += 1
        try:
            yield {"type": "text", "text": narration}
            for index, (name, args) in enumerate(
                [
                    ("update_milestone", node),
                    ("set_target", {"statement": target}),
                ]
            ):
                yield {
                    "type": "tool_delta",
                    "index": index,
                    "id": str(index),
                    "name": name,
                    "arguments": json.dumps(args),
                }
            if failure_source == "tool":
                yield {
                    "type": "tool_delta",
                    "index": 2,
                    "id": "read",
                    "name": "read_project",
                    "arguments": "{}",
                }
            else:
                raise httpx.ReadTimeout(SECRET)
        finally:
            closed = True

    app.settings.stream = stream
    events = collect(app, planned.id, mode)
    assert calls == 1 and closed
    errors = [event for event in events if event["type"] == "error"]
    assert errors[0]["code"] == (
        "httpx_read_timeout" if failure_source == "provider" else "internal_type_error"
    )
    assert len(errors) == (2 if finalize_fails else 1)
    saved = app.db.get(planned.id)
    assert saved.milestone("M01").title == node["title"]
    if finalize_fails:
        assert saved.target_draft == target
    else:
        assert saved.targets[-1].statement == target and saved.target_draft is None
    assert app.db.messages(planned.id)[-1]["content"] == narration
    done = events[-1]
    assert done["type"] == "done" and done["changed"]
    assert done["summary"]["status"] == "failed" and done["summary"]["changed"]
    assert done["project"]["messages"][-1]["content"] == narration
    result = app.agent.turn_result(planned.id, done["turn_id"])
    assert result["summary"] == done["summary"] and not result["pending"]
    assert not app.operation_lock(planned.id).locked()
    assert SECRET not in json.dumps(events) + json.dumps(app.db.events(planned.id)) + caplog.text
