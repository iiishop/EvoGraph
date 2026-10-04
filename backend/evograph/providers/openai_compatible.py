from urllib.parse import urlsplit

import httpx

from .base import check_url, register
from .streaming import sse_payloads


class _DoneObservedResponse:
    """Observe the SSE sentinel without changing the shared JSON framing parser."""
    def __init__(self, response):
        self.response = response
        self.received_done_marker = False

    async def aiter_lines(self):
        async for line in self.response.aiter_lines():
            if line.startswith("data:") and line[5:].strip() == "[DONE]":
                self.received_done_marker = True
            yield line


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

    @staticmethod
    def validate_request_controls(config, request_controls):
        """Allow only the ephemeral official DeepSeek Flash review experiment."""
        base_url = config["base_url"]
        target = urlsplit(base_url)
        if (
            request_controls != {"reasoning_effort": "low"}
            or config["model"] != "deepseek-flash"
            or target.scheme != "https"
            or target.netloc not in {"api.deepseek.com", "api.deepseek.com:443"}
            or target.path not in {"", "/", "/v1", "/v1/"}
            or "?" in base_url or "#" in base_url
        ):
            raise ValueError("评审 low 实验仅支持官方 HTTPS DeepSeek Flash，未发送请求")
        return {"reasoning_effort": "low"}

    async def stream(self, config, secret, messages, tools, *, request_controls=None):
        check_url(config["base_url"])
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        body = {"model": config["model"], "messages": messages, "tools": tools, "stream": True}
        if request_controls is not None:
            body.update(self.validate_request_controls(config, request_controls))
        # Derive diagnostics from the outgoing body, never from arbitrary config,
        # headers, credentials or an assumed provider/model default.
        controls = ("model", "stream", "max_tokens", "max_completion_tokens", "reasoning_effort",
                    "stream_options", "tool_choice", "parallel_tool_calls", "n")
        limits = {key: body[key] for key in ("max_completion_tokens", "max_tokens") if key in body}
        yield {
            "type": "request_metadata", "provider": "openai_compatible",
            "controls": {key: body[key] for key in controls if key in body},
            "unset_controls": [key for key in controls if key not in body],
            "completion_limit": ({"state": "explicit", "fields": limits} if limits else
                                 {"state": "unset", "server_default": "unknown"}),
            "tool_strict": [{"name": t["function"]["name"],
                             "strict": t["function"].get("strict")} for t in tools],
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            async with client.stream(
                "POST",
                config["base_url"].rstrip("/") + "/chat/completions",
                headers=headers,
                json=body,
            ) as response:
                yield {"type": "response_started", "http_status": response.status_code}
                if response.is_error:
                    raise ValueError(f"Provider 流式请求失败（HTTP {response.status_code}）")
                observed = _DoneObservedResponse(response)
                async for payload in sse_payloads(observed):
                    # Parsed-payload activity only: the SSE helper does not yield
                    # comments, blank keepalives or the terminal sentinel.
                    yield {"type": "stream_activity",
                           "payload_kind": "empty" if not payload else "data"}
                    if payload.get("error"):
                        raise ValueError("Provider 返回流式错误，请检查模型是否支持工具调用")
                    if payload.get("usage"):
                        usage = payload["usage"]
                        allowed = {k: v for k, v in usage.items() if k in {
                            "prompt_tokens", "completion_tokens", "total_tokens",
                            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
                        } and isinstance(v, (int, float)) and not isinstance(v, bool)}
                        for key in ("prompt_tokens_details", "completion_tokens_details"):
                            if isinstance(usage.get(key), dict):
                                allowed[key] = {k: v for k, v in usage[key].items()
                                                if isinstance(v, (int, float)) and not isinstance(v, bool)}
                        yield {"type": "usage", "tokens": usage.get("total_tokens", 0),
                               "usage_counter": "openai_total", "usage_details": allowed}
                    for choice in payload.get("choices", []):
                        delta = choice.get("delta", {})
                        reasoning = delta.get("reasoning_content")
                        if isinstance(reasoning, str) and reasoning:
                            # Keep only volume metadata, never reasoning content.
                            yield {"type": "reasoning_activity",
                                   "utf8_bytes": len(reasoning.encode("utf-8"))}
                        if delta.get("content"):
                            yield {"type": "text", "text": delta["content"],
                                   "choice_index": choice.get("index", 0)}
                        for call in delta.get("tool_calls", []):
                            function = call.get("function", {})
                            yield {
                                "type": "tool_delta",
                                "index": call.get("index", 0),
                                "choice_index": choice.get("index", 0),
                                "id": call.get("id", ""),
                                "name": function.get("name", ""),
                                "arguments": function.get("arguments", ""),
                            }
                        if choice.get("finish_reason") is not None:
                            yield {"type": "response_finish", "choice_index": choice.get("index", 0),
                                   "finish_reason": choice["finish_reason"]}
                yield {"type": "response_end", "normal_stream_end": True,
                       "terminal_marker": "[DONE]", "received_terminal_marker": observed.received_done_marker}
