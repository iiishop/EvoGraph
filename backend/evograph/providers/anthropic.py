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
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            async with client.stream(
                "POST",
                config["base_url"].rstrip("/") + "/v1/messages",
                headers={"x-api-key": secret, "anthropic-version": "2023-06-01"},
                json=body,
            ) as response:
                if response.is_error:
                    raise ValueError(f"Provider 流式请求失败（HTTP {response.status_code}）")
                async for payload in sse_payloads(response):
                    kind = payload.get("type")
                    if kind == "error":
                        raise ValueError("Provider 返回流式错误")
                    if kind == "message_start":
                        yield {
                            "type": "usage",
                            "tokens": payload.get("message", {})
                            .get("usage", {})
                            .get("input_tokens", 0),
                        }
                    if kind == "message_delta":
                        yield {
                            "type": "usage",
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
