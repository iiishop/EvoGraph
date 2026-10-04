"""Offline request/response provenance; no real providers, settings or credentials."""
import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from evograph.application.plan_budget import BudgetedSettings
from evograph.application.plan_patch import PlanPatch
from evograph.application.provider_output import ProviderOutputError
from evograph.providers.anthropic import Anthropic
from evograph.providers.openai_compatible import OpenAICompatible
from pydantic import ValidationError

TOOL = {"type": "function", "function": {"name": "propose_plan_patch", "description": "Fixture",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}


def sse(*payloads):
    return "".join("data: " + (p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)) + "\n\n"
                   for p in payloads).encode("utf-8")


def delta(arguments, *, name="", call_id="", choice=0, index=0):
    return {"choices": [{"index": choice, "delta": {"tool_calls": [{"index": index,
        "id": call_id, "function": {"name": name, "arguments": arguments}}]}}]}


class BytesThenFailure(httpx.AsyncByteStream):
    def __init__(self, raw, failure=None):
        self.raw, self.failure, self.closed = raw, failure, False

    async def __aiter__(self):
        # Split multibyte Unicode as well as SSE framing across transport chunks.
        for i in range(0, len(self.raw), 5):
            yield self.raw[i:i + 5]
        if self.failure:
            raise self.failure

    async def aclose(self):
        self.closed = True


def mock_http(monkeypatch, raw, *, failure=None, status=200):
    sent = []
    stream = BytesThenFailure(raw, failure)
    async def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(status, stream=stream)
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    return sent, stream


def budget(adapter, **limits):
    config = {"base_url": "https://provider.invalid/v1", "model": "mock-model",
              "ignored_extra": "mock-config-secret-never-audited", "max_tokens": 999}
    async def stream(messages, tools):
        async for event in adapter.stream(config, "mock-secret-never-audited", messages, tools):
            yield event
    metrics = {"provider_calls": 0, "tokens": 0, "usage_reported": False, "budget": limits}
    snapshots = []
    settings = BudgetedSettings(SimpleNamespace(stream=stream, secrets=None), metrics,
                                checkpoint=lambda **kwargs: snapshots.append(copy.deepcopy(metrics)))
    return settings, metrics, snapshots


def consume(settings, tools=None):
    async def run():
        return [event async for event in settings.stream([{"role": "user", "content": "Fixture"}], tools or [TOOL])]
    return asyncio.run(run())


def test_openai_actual_request_controls_finish_markers_and_raw_usage(monkeypatch):
    usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
             "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60,
             "prompt_tokens_details": {"cached_tokens": 40},
             "completion_tokens_details": {"reasoning_tokens": 30},
             "ignored_secret": "usage-secret-never-audited"}
    sent, stream = mock_http(monkeypatch, sse(
        {"choices": [{"index": 0, "delta": {"content": "规划"}}]},
        delta('{"milestones":', name="propose_plan_patch", call_id="call-1"), delta("[]}"),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": usage}, {"choices": [], "usage": usage}, "[DONE]"))
    settings, metrics, snapshots = budget(OpenAICompatible())
    events = consume(settings)
    call = metrics["calls"][0]
    assert set(sent[0]) == {"model", "messages", "tools", "stream"}
    assert sent[0]["tools"] == [TOOL]  # No silent strict-mode change.
    assert call["request_metadata"]["controls"] == {"model": "mock-model", "stream": True}
    assert {"max_tokens", "max_completion_tokens", "reasoning_effort", "stream_options"} <= set(call["request_metadata"]["unset_controls"])
    assert call["request_metadata"]["completion_limit"] == {"state": "unset", "server_default": "unknown"}
    assert call["request_metadata"]["tool_strict"] == [{"name": "propose_plan_patch", "strict": None}]
    assert call["status"] == "stream_ended" and call["normal_stream_end"]
    assert call["received_finish_marker"] and call["received_terminal_marker"]
    assert call["response_finish"] == [{"choice_index": 0, "finish_reason": "tool_calls"}]
    assert call["response_audit"]["text"] == "规划"
    assert call["response_audit"]["tool_calls"][0]["arguments"] == '{"milestones":[]}'
    assert call["response_audit"]["tool_calls"][0]["fragment_count"] == 2
    assert metrics["tokens"] == 150
    assert [e["tokens"] for e in events if e["type"] == "usage"] == [150, 0]
    assert len(call["usage_events"]) == 2
    assert call["usage_events"][1]["tokens"] == 150  # Original cumulative count stays auditable.
    assert call["usage_events"][0]["usage_details"]["completion_tokens_details"]["reasoning_tokens"] == 30
    assert call["usage_events"][0]["usage_details"]["prompt_cache_hit_tokens"] == 40
    assert snapshots[-1]["calls"][0] == call
    assert "never-audited" not in json.dumps(metrics)
    assert "provider.invalid" not in json.dumps(metrics)
    assert stream.closed


@pytest.mark.parametrize("reason, marker", [("length", True), ("tool_calls", False), (None, False), ("tool_calls", True)])
def test_last_invalid_json_is_preserved_without_claiming_completion(monkeypatch, reason, marker):
    payloads = [delta('{"milestones":[', name="propose_plan_patch", call_id="last-call")]
    if reason:
        payloads.append({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]})
    if marker:
        payloads.append("[DONE]")
    mock_http(monkeypatch, sse(*payloads))
    settings, metrics, snapshots = budget(OpenAICompatible())
    if reason == "tool_calls" and marker:
        consume(settings)
    else:
        with pytest.raises(ProviderOutputError) as error:
            consume(settings)
        assert error.value.code == ("provider_output_limited" if reason == "length" else "provider_output_incomplete")
    call = snapshots[-1]["calls"][0]
    raw = call["response_audit"]["tool_calls"][0]["arguments"]
    assert raw == '{"milestones":['
    with pytest.raises(ValidationError, match="Invalid JSON"):
        PlanPatch.model_validate_json(raw)
    assert call["status"] == "stream_ended" and call["normal_stream_end"]
    assert call["received_terminal_marker"] is marker
    assert call["received_finish_marker"] is bool(reason)
    assert call["response_audit"]["capture_complete"]
    assert len(metrics["calls"]) == 1  # No next request needed to preserve the failed final exchange.


@pytest.mark.parametrize("finish_first", [False, True])
def test_transport_interruption_retains_partial_arguments_and_observed_finish(monkeypatch, finish_first):
    payloads = [delta('{"target":"部分', name="propose_plan_patch", call_id="partial")]
    if finish_first:
        payloads.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]})
    _, stream = mock_http(monkeypatch, sse(*payloads), failure=httpx.ReadError("mock-transport-secret-never-audited"))
    settings, metrics, snapshots = budget(OpenAICompatible())
    with pytest.raises(httpx.ReadError):
        consume(settings)
    call = snapshots[-1]["calls"][0]
    assert call["status"] == "interrupted" and not call["normal_stream_end"]
    assert call["error_type"] == "ReadError" and call["termination_reason"] == "provider_error"
    assert call["received_finish_marker"] is finish_first
    assert not call["received_terminal_marker"]
    assert call["response_audit"]["tool_calls"][0]["arguments"] == '{"target":"部分'
    assert "never-audited" not in json.dumps(metrics)
    assert stream.closed


def test_service_error_retains_prior_output_and_closes_provider(monkeypatch):
    _, stream = mock_http(monkeypatch, sse(
        delta('{"target":', name="propose_plan_patch"), {"error": {"message": "service rejected"}}))
    settings, metrics, snapshots = budget(OpenAICompatible())
    with pytest.raises(ValueError, match="流式错误"):
        consume(settings)
    call = snapshots[-1]["calls"][0]
    assert call["status"] == "interrupted" and call["error_type"] == "ValueError"
    assert call["response_audit"]["tool_calls"][0]["arguments"] == '{"target":'
    assert stream.closed


def test_cancellation_checkpoints_output_received_before_close(monkeypatch):
    mock_http(monkeypatch, sse(delta('{"summary":"未完', name="propose_plan_patch", call_id="cancel"), "[DONE]"))
    settings, metrics, snapshots = budget(OpenAICompatible())
    async def cancel():
        worker = settings.stream([], [TOOL])
        while (await anext(worker))["type"] != "tool_delta":
            pass
        await worker.aclose()
    asyncio.run(cancel())
    call = snapshots[-1]["calls"][0]
    assert call["termination_reason"] == "cancelled"
    assert call["status"] == "interrupted" and not call["normal_stream_end"]
    assert call["response_audit"]["tool_calls"][0]["arguments"] == '{"summary":"未完'
    assert not call["received_terminal_marker"]


def test_output_limit_records_only_bounded_utf8_prefix_and_stops_delivery(monkeypatch):
    # name+id consume 2 bytes, leaving 8; two whole Chinese characters fit.
    _, stream = mock_http(monkeypatch, sse(delta("测" * 100, name="p", call_id="i"), "[DONE]"))
    settings, metrics, snapshots = budget(OpenAICompatible(), max_output_bytes=10)
    with pytest.raises(ValueError, match="输出"):
        consume(settings)
    call = snapshots[-1]["calls"][0]
    audit = call["response_audit"]
    assert call["termination_reason"] == "output_limit"
    assert call["output_bytes"] == 302 and audit["captured_output_bytes"] == 8
    assert audit["tool_calls"][0]["arguments"] == "测测"
    assert not audit["capture_complete"] and audit["omitted_output_bytes"] == 294
    assert not call["received_terminal_marker"] and stream.closed


def test_tool_choice_indexes_are_preserved_separately_for_framing_diagnosis(monkeypatch):
    mock_http(monkeypatch, sse(delta("{", name="first", choice=0), delta("[", name="second", choice=1),
                              delta("}", choice=0), delta("]", choice=1), "[DONE]"))
    settings, metrics, _ = budget(OpenAICompatible())
    with pytest.raises(ProviderOutputError) as error:
        consume(settings)
    assert error.value.code == "provider_output_incomplete"
    calls = metrics["calls"][0]["response_audit"]["tool_calls"]
    assert [(c["choice_index"], c["index"], c["name"], c["arguments"]) for c in calls] == [
        (0, 0, "first", "{"), (1, 0, "second", "[")]
    assert not metrics["calls"][0]["normal_stream_end"]


@pytest.mark.parametrize("reason, marker", [("tool_use", True), ("max_tokens", True), ("end_turn", False)])
def test_anthropic_actual_max_tokens_stop_reason_and_message_stop(monkeypatch, reason, marker):
    payloads = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 30}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "a", "name": "propose_plan_patch", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"milestones":[]}' }},
        {"type": "message_delta", "delta": {"stop_reason": reason}, "usage": {"output_tokens": 25}},
    ]
    if marker:
        payloads.append({"type": "message_stop"})
    sent, stream = mock_http(monkeypatch, sse(*payloads))
    settings, metrics, _ = budget(Anthropic())
    if reason == "tool_use" and marker:
        consume(settings)
    else:
        with pytest.raises(ProviderOutputError) as error:
            consume(settings)
        assert error.value.code == ("provider_output_limited" if reason == "max_tokens" else "provider_output_incomplete")
    call = metrics["calls"][0]
    assert sent[0]["max_tokens"] == 8000  # Existing adapter default remains unchanged.
    assert "thinking" not in sent[0]
    assert call["request_metadata"]["controls"]["max_tokens"] == 8000
    assert call["request_metadata"]["completion_limit"] == {"state": "explicit", "field": "max_tokens", "value": 8000}
    assert call["response_finish"] == [{"stop_reason": reason}]
    assert call["received_terminal_marker"] is marker
    assert call["received_finish_marker"] and call["normal_stream_end"]
    assert call["status"] == "stream_ended" and metrics["tokens"] == 125
    assert call["usage_events"][0]["usage_details"]["cache_read_input_tokens"] == 30
    assert call["response_audit"]["tool_calls"][0]["arguments"] == '{"milestones":[]}'
    assert stream.closed


def test_http_error_keeps_attempted_controls_and_confirms_response_without_body_secrets(monkeypatch):
    mock_http(monkeypatch, b"mock-error-body-secret-never-audited", status=429)
    settings, metrics, snapshots = budget(OpenAICompatible())
    with pytest.raises(ValueError, match="HTTP 429"):
        consume(settings)
    call = snapshots[-1]["calls"][0]
    assert call["provider_response_received"] and call["http_status"] == 429
    assert call["request_metadata"]["completion_limit"]["state"] == "unset"
    assert call["response_audit"]["tool_calls"] == []
    assert call["status"] == "interrupted"
    assert "never-audited" not in json.dumps(metrics)


def test_failure_before_response_does_not_claim_provider_received_request(monkeypatch):
    async def fail(request):
        raise httpx.ConnectError("mock-connection-secret-never-audited")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(fail), **kwargs))
    settings, metrics, snapshots = budget(OpenAICompatible())
    with pytest.raises(httpx.ConnectError):
        consume(settings)
    call = snapshots[-1]["calls"][0]
    assert call["request_metadata"]["controls"] == {"model": "mock-model", "stream": True}
    assert not call["provider_response_received"] and "http_status" not in call
    assert call["status"] == "interrupted" and call["error_type"] == "ConnectError"
    assert "never-audited" not in json.dumps(metrics)


@pytest.mark.parametrize("reason", ["content_filter", "error", "unknown", {"unsupported": True}])
def test_unknown_filtered_or_error_finish_is_not_accepted(monkeypatch, reason):
    mock_http(monkeypatch, sse(delta("{}", name="propose_plan_patch"),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}, "[DONE]"))
    settings, metrics, _ = budget(OpenAICompatible())
    with pytest.raises(ProviderOutputError) as error:
        consume(settings)
    assert error.value.code == "provider_output_incomplete"
    call = metrics["calls"][0]
    assert call["status"] == "stream_ended" and call["normal_stream_end"]
    assert call["termination_reason"] == "provider_output_incomplete"
    assert call["response_finish"][0]["finish_reason"] == reason


@pytest.mark.parametrize("code", ["provider_output_limited", "provider_output_incomplete"])
def test_output_integrity_public_errors_use_only_fixed_codes_and_messages(code):
    from evograph.application.agent_errors import agent_error_event
    error = ProviderOutputError(code)
    error.args = ("mock-private-error-never-audited",)
    error.__cause__ = RuntimeError("mock-private-cause-never-audited")
    event = agent_error_event(error)
    assert event["code"] == code and code in event["message"]
    assert "never-audited" not in json.dumps(event)


@pytest.mark.parametrize("reason, marker", [("length", True), (None, True), ("tool_calls", False)])
def test_valid_patch_followed_by_bad_termination_never_reviews_or_commits(app, reason, marker):
    from test_unified_planning import collect, delta_fixture, enable
    p = enable(app)
    arguments = json.dumps(delta_fixture())
    calls = []
    async def stream(messages, tools):
        calls.append(tools)
        assert tools[0]["function"]["name"] != "submit_plan_review"
        yield {"type": "request_metadata", "provider": "openai_compatible", "controls": {"stream": True},
               "completion_limit": {"state": "unset", "server_default": "unknown"}}
        yield {"type": "tool_delta", "index": 0, "choice_index": 0, "id": "complete-call",
               "name": "submit_plan_delta", "arguments": arguments}
        if reason:
            yield {"type": "response_finish", "choice_index": 0, "finish_reason": reason}
        yield {"type": "response_end", "terminal_marker": "[DONE]", "received_terminal_marker": marker}
    app.settings.stream = stream
    events = collect(app, p)
    record = app.unified.store.latest(p.id)
    assert len(calls) == 1 and not record["reviews"]
    assert record["project"]["milestones"]
    assert record["tool_attempts"][0]["status"] == "succeeded"
    assert record["status"] == "failed" and app.db.get(p.id) == p
    expected = "provider_output_limited" if reason == "length" else "provider_output_incomplete"
    assert any(e.get("code") == expected for e in events if e["type"] == "error")
    call = record["metrics"]["calls"][0]
    assert call["normal_stream_end"] and call["status"] == "stream_ended"
    assert call["response_audit"]["tool_calls"][0]["arguments"] == arguments
    assert call["output_integrity"] == {"status": "rejected", "code": expected}


def test_integrity_gate_does_not_change_direct_legacy_adapter_stream(monkeypatch):
    mock_http(monkeypatch, sse(delta("{}", name="propose_plan_patch"),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}, "[DONE]"))
    async def run():
        return [e async for e in OpenAICompatible().stream(
            {"base_url": "https://provider.invalid/v1", "model": "mock"}, "", [], [TOOL])]
    events = asyncio.run(run())
    assert any(e.get("finish_reason") == "length" for e in events)
    assert events[-1]["type"] == "response_end"
