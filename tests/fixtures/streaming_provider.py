"""Local deterministic protocol fixture, never a production fallback model.

Run: python tests/fixtures/streaming_provider.py
Use http://127.0.0.1:9876/v1, model=fixture, no API key in an isolated workspace.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def node(node_id, title):
    return {
        "id": node_id,
        "title": title,
        "intent": "本地流式协议测试节点",
        "scope": ["auth.py"],
        "behaviors": [{"key": f"test.{node_id}", "statement": title + "可被验证"}],
        "resources": [f"api:{node_id}"],
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode())
            return
        messages = body["messages"]
        latest = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if messages[-1]["role"] == "tool":
            calls = []
        elif "提问" in latest:
            calls = [
                (
                    "ask_user",
                    {
                        "prompt": "新登录功能应使用哪种身份？",
                        "options": ["邮箱", "用户名"],
                        "context": "这个选择会改变登录行为的验收条件。",
                    },
                )
            ]
        elif "更新" in latest:
            calls = [("update_milestone", node("N01", "邮箱验证服务（已更新）"))]
        else:
            calls = [
                ("create_milestone", node("N01", "邮箱验证服务")),
                (
                    "create_milestone",
                    {
                        **node("N02", "验证用户流程"),
                        "dependencies": ["N01"],
                        "dependency_reasons": {"N01": "需要邮箱验证服务"},
                    },
                ),
                (
                    "add_dependency",
                    {
                        "source": "N01",
                        "target": "N02",
                        "reason": "验证前置能力",
                        "kind": "verification",
                    },
                ),
            ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for index, (name, args) in enumerate(calls):
                self.emit(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": index,
                                            "id": f"call_{index}",
                                            "function": {"name": name, "arguments": ""},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )
                value = json.dumps(args, ensure_ascii=False)
                for offset in range(0, len(value), 16):
                    self.emit(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": index,
                                                "function": {
                                                    "arguments": value[offset : offset + 16]
                                                },
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    )
                    time.sleep(0.025)
            if not calls:
                self.emit({"choices": [{"delta": {"content": "测试操作完成"}}]})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def emit(self, payload):
        self.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode())
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 9876), Handler).serve_forever()
