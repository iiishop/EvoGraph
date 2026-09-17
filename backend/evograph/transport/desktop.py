import asyncio
import json
import threading
from pathlib import Path

from ..application.api import Application
from .assets import configure_asset_types


class DesktopBridge:
    def __init__(self, application: Application):
        self._application = application
        self._window = None
        self._streams = {}
        self._guard = threading.Lock()
        self._cancelled = set()

    def command(self, action: str, params: dict):
        return asyncio.run(self._application.dispatch(action, params))

    def start_agent(self, request_id: str, params: dict):
        from .http import AgentRequest

        request = AgentRequest.model_validate(params)
        with self._guard:
            if request_id in self._streams:
                raise ValueError("重复的流请求")
            self._streams[request_id] = None

        def run():
            async def consume():
                loop, task = asyncio.get_running_loop(), asyncio.current_task()
                with self._guard:
                    self._streams[request_id] = (loop, task)
                    cancelled = request_id in self._cancelled
                try:
                    if cancelled:
                        return
                    async for event in self._application.agent.stream(**request.model_dump()):
                        detail = json.dumps(
                            {"request_id": request_id, "event": event}, ensure_ascii=True
                        )
                        self._window.evaluate_js(
                            f"window.dispatchEvent(new CustomEvent('evograph:agent', {{detail: {detail}}}))"
                        )
                except asyncio.CancelledError:
                    pass
                finally:
                    with self._guard:
                        self._cancelled.discard(request_id)
                        self._streams.pop(request_id, None)

            asyncio.run(consume())

        threading.Thread(target=run, daemon=True).start()
        return {"started": True}

    def cancel_agent(self, request_id: str):
        with self._guard:
            stream = self._streams.get(request_id)
            if request_id in self._streams:
                self._cancelled.add(request_id)
        if stream:
            loop, task = stream
            loop.call_soon_threadsafe(task.cancel)
        return {"cancelled": True}


def launch(application: Application, dist: Path):
    import webview

    configure_asset_types()
    index = dist / "index.html"
    if not index.exists():
        raise SystemExit("前端尚未构建。请先运行 npm install 和 npm run build。")
    bridge = DesktopBridge(application)
    # Pass the absolute file path; pywebview's http_server serves it through its
    # local origin and injects the bridge before the Vue app starts.
    window = webview.create_window(
        "EvoGraph · 项目演化工作台",
        str(index),
        js_api=bridge,
        width=1480,
        height=960,
        min_size=(1050, 720),
        background_color="#f7f8fa",
    )
    bridge._window = window
    webview.start(http_server=True)
