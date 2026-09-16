import asyncio

import httpx
from evograph.providers.streaming import anthropic_messages, sse_payloads


class FragmentedBytes(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        for i in range(0, len(self.content), 3):
            yield self.content[i : i + 3]


def test_sse_handles_utf8_split_across_network_chunks():
    raw = 'data: {"text":"邮箱登录"}\n\ndata: [DONE]\n\n'.encode()

    async def run():
        response = httpx.Response(200, stream=FragmentedBytes(raw))
        return [item async for item in sse_payloads(response)]

    assert asyncio.run(run()) == [{"text": "邮箱登录"}]


def test_anthropic_tool_result_conversion():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "function": {"name": "read_project", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
    ]
    result = anthropic_messages(messages)
    assert result[0]["content"][0]["type"] == "tool_use"
    assert result[1]["content"][0]["tool_use_id"] == "call-1"
