"""Opt-in native WebView integration smoke test; uses disposable project data.

Run from the checkout: QT_QPA_PLATFORM=offscreen \
    uv run python tools/smoke_desktop.py
Offscreen success does not verify a visible desktop session. No model calls or keys.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "backend"))
import webview
from evograph.application.api import Application
from evograph.transport.desktop import launch

start = webview.start
result = {}


def inspect():
    window = webview.windows[0]
    if not window.events.loaded.wait(25):
        result["error"] = "document did not load"
    else:
        for _ in range(100):
            text = window.evaluate_js("document.body.innerText")
            if "认证工作台" in text and "正在打开工作空间" not in text:
                break
            time.sleep(0.1)
        result["document"] = window.evaluate_js(
            "({title: document.title, text: document.body.innerText, bridge: !!window.pywebview})"
        )
        result["projects"] = window._js_api.command("projects.list", {})
        result["create"] = window._js_api.command(
            "projects.create", {"name": "Linux native smoke", "request_id": "linux-native-smoke"}
        )
        result["settings"] = window._js_api.command("settings.get", {})
    print("NATIVE_SMOKE_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    window.destroy()


def wrapped(**kwargs):
    return start(inspect, **kwargs)


webview.start = wrapped
with tempfile.TemporaryDirectory(prefix="evograph-desktop-smoke-") as data_dir:
    launch(Application(Path(data_dir)), Path.cwd() / "dist")
if not (
    result.get("document", {}).get("bridge")
    and "里程碑图" in result["document"]["text"]
    and "M07" in result["document"]["text"]
    and "正在打开工作空间" not in result["document"]["text"]
    and result.get("create", {}).get("ok")
    and result.get("settings", {}).get("ok")
):
    raise SystemExit(1)
