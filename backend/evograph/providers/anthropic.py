import httpx

from .base import check_url, register
from .streaming import anthropic_messages, sse_payloads


@register
class Anthropic:
    descriptor = {
        "id": "anthropic",
        "name": "Anthropic Messages",
        "description": "兼容 Anthropic Messages API 的模型服务",
        "fields": [
            {
                "key": "base_url",
                "label": "Base URL",
                "placeholder": "https://api.anthropic.com",
                "default": "https://api.anthropic.com",
                "required": True,
            },
            {
                "key": "model",
                "label": "模型",
                "placeholder": "输入服务商提供的模型 ID",
                "default": "",
                "required": True,
            },
        ],
        "secret_label": "API Key",
    }

    async def complete(self, config, secret, messages):
        check_url(config["base_url"])
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            response = await client.post(
                config["base_url"].rstrip("/") + "/v1/messages",
                headers={"x-api-key": secret, "anthropic-version": "2023-06-01"},
                json={
                    "model": config["model"],
                    "max_tokens": 6000,
                    "system": system,
                    "messages": [m for m in messages if m["role"] != "system"],
                },
            )
            if response.is_error:
                raise ValueError(
                    f"Provider 请求失败（HTTP {response.status_code}）。请检查地址、模型与密钥。"
                )
            payload = response.json()
        content = "\n".join(
            b["text"] for b in payload.get("content", []) if b.get("type") == "text"
        )
        if not content:
            raise ValueError("Provider 没有返回文本内容")
        usage = payload.get("usage", {})
        return content, usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

    async def stream(self, config, secret, messages, tools):
        check_url(config["base_url"])
        functions = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]
        body = {
            "model": config["model"],
            "max_tokens": 8000,
            "stream": True,
            "tools": functions,
            "system": "\n".join(m["content"] for m in messages if m["role"] == "system"),
            "messages": anthropic_messages(messages),
        }
        controls = ("model", "stream", "max_tokens", "thinking", "tool_choice", "temperature", "top_p")
        yield {
            "type": "request_metadata", "provider": "anthropic",
            "controls": {key: body[key] for key in controls if key in body},
            "unset_controls": [key for key in controls if key not in body],
            "completion_limit": {"state": "explicit", "field": "max_tokens", "value": body["max_tokens"]},
            "tool_strict": [{"name": t["name"], "strict": t.get("strict")} for t in functions],
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            async with client.stream(
                "POST",
                config["base_url"].rstrip("/") + "/v1/messages",
                headers={"x-api-key": secret, "anthropic-version": "2023-06-01"},
                json=body,
            ) as response:
                yield {"type": "response_started", "http_status": response.status_code}
                if response.is_error:
                    raise ValueError(f"Provider 流式请求失败（HTTP {response.status_code}）")
                received_message_stop = False
                async for payload in sse_payloads(response):
                    kind = payload.get("type")
                    # JSON ping events are observable; SSE comments and blank
                    # keepalives are not yielded by the shared framing helper.
                    yield {"type": "stream_activity", "payload_kind":
                           "ping" if kind == "ping" else "empty" if not payload else "data"}
                    if kind == "error":
                        raise ValueError("Provider 返回流式错误")
                    if kind == "message_start":
                        yield {
                            "type": "usage",
                            "usage_counter": "anthropic_input",
                            "usage_details": {k: v for k, v in payload.get("message", {}).get("usage", {}).items()
                                              if isinstance(v, (int, float)) and not isinstance(v, bool)},
                            "tokens": payload.get("message", {})
                            .get("usage", {})
                            .get("input_tokens", 0),
                        }
                    if kind == "message_delta":
                        if payload.get("delta", {}).get("stop_reason") is not None:
                            yield {"type": "response_finish",
                                   "stop_reason": payload["delta"]["stop_reason"]}
                        yield {
                            "type": "usage",
                            "usage_counter": "anthropic_output",
                            "usage_details": {k: v for k, v in payload.get("usage", {}).items()
                                              if isinstance(v, (int, float)) and not isinstance(v, bool)},
                            "tokens": payload.get("usage", {}).get("output_tokens", 0),
                        }
                    if kind == "content_block_start":
                        block = payload.get("content_block", {})
                        if block.get("type") == "tool_use":
                            yield {
                                "type": "tool_delta",
                                "index": payload["index"],
                                "id": block["id"],
                                "name": block["name"],
                                "arguments": "",
                            }
                    if kind == "content_block_delta":
                        delta = payload.get("delta", {})
                        if delta.get("type") == "thinking_delta":
                            thinking = delta.get("thinking")
                            if isinstance(thinking, str) and thinking:
                                # Do not forward or persist reasoning text.
                                yield {"type": "reasoning_activity",
                                       "utf8_bytes": len(thinking.encode("utf-8"))}
                        if delta.get("type") == "text_delta":
                            yield {"type": "text", "text": delta["text"]}
                        elif delta.get("type") == "input_json_delta":
                            yield {
                                "type": "tool_delta",
                                "index": payload["index"],
                                "arguments": delta["partial_json"],
                                "id": "",
                                "name": "",
                            }
                    if kind == "message_stop":
                        received_message_stop = True
                yield {"type": "response_end", "normal_stream_end": True,
                       "terminal_marker": "message_stop", "received_terminal_marker": received_message_stop}
