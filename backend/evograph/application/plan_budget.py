"""Explicit request-volume budgets and auditable usage, without guessing prices."""
import asyncio
import copy
import hashlib
import json
import time
from contextlib import aclosing

from .provider_output import ProviderOutputError, require_complete_provider_output


class BudgetExceededError(ValueError):
    """Request admission/output budget ended this run, not model uncertainty."""


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def compact_schema(schema):
    """Drop schema-node title annotations without changing named data properties."""
    if isinstance(schema, dict):
        result = {}
        for key, value in schema.items():
            if key == "title":
                continue
            if key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}:
                result[key] = {name: compact_schema(child) for name, child in value.items()}
            elif key in {"default", "const", "enum", "examples"}:
                result[key] = copy.deepcopy(value)
            else:
                result[key] = compact_schema(value)
        return result
    if isinstance(schema, list):
        return [compact_schema(x) for x in schema]
    return schema


def _capture_output(audit, event, remaining_bytes):
    """Keep raw per-channel strings, even invalid JSON, within the output cap.

    Aggregating fragments avoids an unbounded audit list of tiny/empty deltas.
    Choice and tool indexes remain separate so adapter/runtime merging mistakes
    can be diagnosed without normalizing or attempting to repair model output.
    """
    audit["output_event_count"] += 1
    fields = ("text",) if event["type"] == "text" else ("id", "name", "arguments")
    captured = {}
    for key in fields:
        value = str(event.get(key, ""))
        raw = value.encode("utf-8")
        prefix = raw[:max(0, remaining_bytes)].decode("utf-8", errors="ignore")
        count = len(prefix.encode("utf-8"))
        captured[key] = prefix
        remaining_bytes -= count
        audit["captured_output_bytes"] += count
        if count != len(raw):
            audit["capture_complete"] = False
            audit["omitted_output_bytes"] += len(raw) - count
    if event["type"] == "text":
        audit["text"] += captured["text"]
        return
    choice, index = event.get("choice_index", 0), event.get("index", 0)
    target = next((t for t in audit["tool_calls"]
                   if t["choice_index"] == choice and t["index"] == index), None)
    if target is None:
        if not any(captured.values()):
            return  # Empty frames cannot allocate an unbounded number of entries.
        target = {"choice_index": choice, "index": index, "id": "", "name": "", "arguments": "",
                  "fragment_count": 0, "first_event": audit["output_event_count"]}
        audit["tool_calls"].append(target)
    target["fragment_count"] += 1
    target["last_event"] = audit["output_event_count"]
    for key in fields:
        target[key] += captured[key]


class BudgetedSettings:
    def __init__(self, settings, metrics, checkpoint=None, *, request_controls=None, purpose="generation"):
        if purpose not in {"generation", "semantic_review"}:
            raise ValueError("Unknown planning request purpose")
        self.settings, self.metrics, self.checkpoint = settings, metrics, checkpoint
        self.secrets = settings.secrets
        self.request_controls = dict(request_controls) if request_controls is not None else None
        self.purpose = purpose

    async def stream(self, messages, tools):
        tools = [compact_schema(t) for t in tools]
        request = {"messages": messages, "tools": tools}
        options = {}
        if self.request_controls is not None:
            request["request_controls"] = copy.deepcopy(self.request_controls)
            options["request_controls"] = self.request_controls
        request_bytes = encoded_size(request)
        budget = self.metrics.get("budget", {})
        request_limit = budget.get("max_request_bytes", 98304)
        if self.purpose == "semantic_review":
            request_limit = budget.get("max_review_request_bytes", request_limit)
        request_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        rejection = None
        if self.metrics["provider_calls"] >= budget.get("max_calls", 5):
            rejection = ("call_limit", "已到达统一规划的总模型调用上限，候选保留，未自动重试")
        elif request_bytes > request_limit:
            label = "评审" if self.purpose == "semantic_review" else "生成"
            rejection = ("request_input_limit", f"完整{label}输入 {request_bytes} B 超过单请求预算 {request_limit} B；候选保留，未截断或发送")
        elif self.metrics.get("input_bytes", 0) + request_bytes > budget.get("max_total_input_bytes", 262144):
            rejection = ("total_input_limit", "累计输入已达到本轮预算，候选保留，未发送额外模型请求")
        if rejection:
            self.metrics.setdefault("admission_rejections", []).append({
                "purpose": self.purpose, "input_bytes": request_bytes,
                "request_limit_bytes": request_limit, "request_sha256": request_hash,
                "reason": rejection[0], "dispatched": False,
                "admitted_input_bytes": self.metrics.get("input_bytes", 0),
                "admitted_calls": self.metrics["provider_calls"],
            })
            if self.checkpoint:
                try:
                    self.checkpoint()
                except Exception as checkpoint_error:
                    # A terminal race must not replace this admission outcome.
                    # The coordinator retains late metrics without reviving a
                    # discarded candidate, as with post-dispatch audit errors.
                    self.metrics["admission_rejections"][-1]["audit_checkpoint_error_type"] = type(checkpoint_error).__name__
            raise BudgetExceededError(rejection[1])
        call = {
            "number": self.metrics["provider_calls"] + 1,
            "purpose": self.purpose, "request_limit_bytes": request_limit,
            "input_bytes": request_bytes, "schema_bytes": encoded_size(tools),
            "message_bytes": encoded_size(messages),
            "roles_bytes": {role: sum(encoded_size(m) for m in messages if m["role"] == role)
                            for role in {m["role"] for m in messages}},
            "request_sha256": request_hash,
            "output_bytes": 0, "usage_events": [], "status": "admitted", "dispatched": False,
            "request_metadata": None, "response_finish": [], "provider_response_received": False,
            "received_finish_marker": False, "received_terminal_marker": False,
            "normal_stream_end": False,
            # These overlapping activity counters are not usage/token counters.
            # Absence of known reasoning chunks says nothing about internal
            # provider reasoning, unknown fields, comments or blank heartbeats.
            "stream_activity": {
                "coverage": "parsed_sse_payloads_only",
                "reasoning_coverage": "known_streamed_fields_only",
                "payload_count": 0, "empty_payload_count": 0, "ping_payload_count": 0,
                "first_payload_seconds": None, "last_payload_seconds": None,
                "reasoning_chunk_count": 0, "reasoning_utf8_bytes": 0,
                "first_reasoning_seconds": None, "last_reasoning_seconds": None,
            },
            "response_audit": {"text": "", "tool_calls": [], "output_event_count": 0,
                               "captured_output_bytes": 0, "capture_complete": True,
                               "omitted_output_bytes": 0},
        }
        self.metrics.setdefault("calls", []).append(call)
        self.metrics["provider_calls"] += 1
        self.metrics["input_bytes"] = self.metrics.get("input_bytes", 0) + request_bytes
        if self.checkpoint:
            self.checkpoint(request=copy.deepcopy(request))
        started = time.monotonic()
        cumulative = {}
        active_error = None
        try:
            yield {"type": "request_started", "number": call["number"]}
            call["dispatched"] = True
            call["status"] = "running"
            self.metrics["dispatched_calls"] = self.metrics.get("dispatched_calls", 0) + 1
            self.metrics["dispatched_input_bytes"] = self.metrics.get("dispatched_input_bytes", 0) + request_bytes
            if self.checkpoint:
                self.checkpoint()
            async with asyncio.timeout(budget.get("call_timeout_seconds", 180)):
                async with aclosing(self.settings.stream(messages, tools, **options)) as stream:
                    async for event in stream:
                        if event["type"] == "usage":
                            self.metrics["usage_reported"] = True
                            call["usage_events"].append(copy.deepcopy(event))
                            mode = event.get("usage_counter")
                            amount = event["tokens"]
                            if mode:
                                # Adapters identify cumulative counters independently,
                                # e.g. Anthropic input and output are not one counter.
                                previous = cumulative.get(mode, 0)
                                cumulative[mode] = max(previous, amount)
                                amount = max(0, amount - previous)
                            self.metrics["tokens"] += amount
                            event = {**event, "tokens": amount}
                        elif event["type"] in {"text", "tool_delta"}:
                            limit = budget.get("max_output_bytes", 98304)
                            _capture_output(call["response_audit"], event, limit - call["output_bytes"])
                            call["output_bytes"] += sum(len(str(event.get(key, "")).encode())
                                                        for key in ("text", "name", "id", "arguments"))
                            if call["output_bytes"] > limit:
                                call["termination_reason"] = "output_limit"
                                raise BudgetExceededError("模型输出达到本轮预算，候选已保留，未自动应用")
                        elif event["type"] == "request_metadata":
                            call["request_metadata"] = copy.deepcopy({k: event[k] for k in (
                                "provider", "controls", "unset_controls", "completion_limit", "tool_strict"
                            ) if k in event})
                        elif event["type"] == "response_started":
                            call["provider_response_received"] = True
                            call["http_status"] = event.get("http_status")
                            call["response_started_seconds"] = round(time.monotonic() - started, 3)
                        elif event["type"] == "stream_activity":
                            activity = call["stream_activity"]
                            elapsed = round(time.monotonic() - started, 3)
                            activity["payload_count"] += 1
                            if event.get("payload_kind") in {"empty", "ping"}:
                                activity[f"{event['payload_kind']}_payload_count"] += 1
                            if activity["first_payload_seconds"] is None:
                                activity["first_payload_seconds"] = elapsed
                            activity["last_payload_seconds"] = elapsed
                        elif event["type"] == "reasoning_activity":
                            size = event.get("utf8_bytes")
                            if type(size) is int and size > 0:
                                activity = call["stream_activity"]
                                elapsed = round(time.monotonic() - started, 3)
                                activity["reasoning_chunk_count"] += 1
                                activity["reasoning_utf8_bytes"] += size
                                if activity["first_reasoning_seconds"] is None:
                                    activity["first_reasoning_seconds"] = elapsed
                                activity["last_reasoning_seconds"] = elapsed
                        elif event["type"] == "response_finish":
                            call["response_finish"].append(copy.deepcopy({k: event[k] for k in (
                                "choice_index", "finish_reason", "stop_reason"
                            ) if k in event}))
                            call["received_finish_marker"] = True
                        elif event["type"] == "response_end":
                            call["received_terminal_marker"] = bool(event.get("received_terminal_marker"))
                            call["terminal_marker"] = event.get("terminal_marker")
                        if call["request_metadata"] is not None and "choice_index" in event:
                            # The runtime supports a single choice and otherwise
                            # merges tool indexes; reject before it receives this delta.
                            if type(event["choice_index"]) is not int or event["choice_index"] != 0:
                                raise ProviderOutputError("provider_output_incomplete")
                        yield event
            # A drained stream is not proof of complete JSON or successful tools.
            # Even finish_reason=length and a missing sentinel can reach this state.
            call["status"] = "stream_ended"
            call["normal_stream_end"] = True
            call["termination_reason"] = "stream_end"
            require_complete_provider_output(call)
            if call["request_metadata"] is not None:
                call["output_integrity"] = {"status": "finish_markers_accepted"}
        except BaseException as exc:
            active_error = exc
            if not call["normal_stream_end"]:
                call["status"] = "interrupted" if call["dispatched"] else "cancelled_before_dispatch"
            call["error_type"] = type(exc).__name__
            if isinstance(exc, ProviderOutputError):
                call["termination_reason"] = exc.code
                call["output_integrity"] = {"status": "rejected", "code": exc.code}
            else:
                call.setdefault("termination_reason", "cancelled" if isinstance(exc, (asyncio.CancelledError, GeneratorExit))
                                else "timeout" if isinstance(exc, TimeoutError) else "provider_error")
            raise
        finally:
            call["elapsed_seconds"] = round(time.monotonic() - started, 3)
            activity = call["stream_activity"]
            for kind in ("payload", "reasoning"):
                last = activity[f"last_{kind}_seconds"]
                activity[f"seconds_since_last_{kind}"] = (
                    round(call["elapsed_seconds"] - last, 3) if last is not None else None
                )
            if self.checkpoint:
                try:
                    self.checkpoint()
                except Exception as checkpoint_error:
                    # A terminal/discard race cannot replace the real cancellation
                    # or provider failure. The outer owner persists late audit.
                    call["audit_checkpoint_error_type"] = type(checkpoint_error).__name__
                    if active_error is None:
                        raise
