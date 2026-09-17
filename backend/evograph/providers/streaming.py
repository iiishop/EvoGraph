"""SSE framing and protocol-neutral events shared by streaming adapters."""

import json


async def sse_payloads(response):
    lines = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            lines.append(line[5:].lstrip())
        elif not line and lines:
            payload = "\n".join(lines)
            lines = []
            if payload == "[DONE]":
                return
            yield json.loads(payload)
    if lines:
        payload = "\n".join(lines)
        if payload != "[DONE]":
            yield json.loads(payload)


def anthropic_messages(messages):
    converted = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        if role == "tool":
            item = {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                ],
            }
        elif role == "assistant" and message.get("tool_calls"):
            blocks = (
                [{"type": "text", "text": message["content"]}] if message.get("content") else []
            )
            for call in message["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": json.loads(call["function"]["arguments"]),
                    }
                )
            item = {"role": "assistant", "content": blocks}
        else:
            content = message["content"]
            blocks = []
            for block in (
                content
                if isinstance(content, list)
                else [{"type": "text", "text": content or "Continue."}]
            ):
                if block["type"] == "image_url":
                    header, data = block["image_url"]["url"].split(",", 1)
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": header[5:].split(";")[0],
                                "data": data,
                            },
                        }
                    )
                else:
                    blocks.append(block)
            item = {"role": role, "content": blocks}
        if converted and converted[-1]["role"] == item["role"]:
            converted[-1]["content"].extend(item["content"])
        else:
            converted.append(item)
    return converted
