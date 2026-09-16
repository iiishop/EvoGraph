import httpx

from .base import check_url, register
from .streaming import sse_payloads


@register
class OpenAICompatible:
    descriptor = {
        "id": "openai_compatible",
        "name": "OpenAI Compatible",
        "description": "兼容 Chat Completions 的云端与本地模型服务",
        "fields": [
            {
                "key": "base_url",
                "label": "Base URL",
                "placeholder": "https://api.example.com/v1",
                "default": "",
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
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            response = await client.post(
                config["base_url"].rstrip("/") + "/chat/completions",
                headers=headers,
                json={"model": config["model"], "messages": messages},
            )
            if response.is_error:
                raise ValueError(
                    f"Provider 请求失败（HTTP {response.status_code}）。请检查地址、模型与密钥。"
                )
            payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            return content, payload.get("usage", {}).get("total_tokens", 0)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError("Provider 返回了不支持的响应格式") from None

    async def stream(self, config, secret, messages, tools):
        check_url(config["base_url"])
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            async with client.stream(
                "POST",
                config["base_url"].rstrip("/") + "/chat/completions",
                headers=headers,
                json={
                    "model": config["model"],
                    "messages": messages,
                    "tools": tools,
                    "stream": True,
                },
            ) as response:
                if response.is_error:
                    raise ValueError(f"Provider 流式请求失败（HTTP {response.status_code}）")
                async for payload in sse_payloads(response):
                    if payload.get("error"):
                        raise ValueError("Provider 返回流式错误，请检查模型是否支持工具调用")
                    if payload.get("usage"):
                        yield {"type": "usage", "tokens": payload["usage"].get("total_tokens", 0)}
                    for choice in payload.get("choices", []):
                        delta = choice.get("delta", {})
                        if delta.get("content"):
                            yield {"type": "text", "text": delta["content"]}
                        for call in delta.get("tool_calls", []):
                            function = call.get("function", {})
                            yield {
                                "type": "tool_delta",
                                "index": call.get("index", 0),
                                "id": call.get("id", ""),
                                "name": function.get("name", ""),
                                "arguments": function.get("arguments", ""),
                            }
