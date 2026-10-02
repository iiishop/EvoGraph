import asyncio
import json
import os
import sys
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
                turn_id = None

                def deliver(event):
                    detail = json.dumps(
                        {"request_id": request_id, "event": event}, ensure_ascii=True
                    )
                    self._window.evaluate_js(
                        f"window.dispatchEvent(new CustomEvent('evograph:agent', {{detail: {detail}}}))"
                    )

                try:
                    if cancelled:
                        # Enter the runtime before cancellation so an admitted turn can
                        # record its stopped outcome instead of leaving the UI waiting.
                        loop.call_soon(task.cancel)
                    async for event in self._application.agent.stream(**request.model_dump()):
                        if event.get("turn_id"):
                            turn_id = event["turn_id"]
                        deliver(event)
                except asyncio.CancelledError:
                    # The runtime has finished committing any in-flight tool and its
                    # terminal summary before cancellation reaches this boundary.
                    result = (
                        self._application.agent.turn_result(request.project_id, turn_id)
                        if turn_id
                        else {"summary": None}
                    )
                    summary = result["summary"]
                    deliver(
                        {
                            "type": "done",
                            "turn_id": turn_id,
                            "summary": summary,
                            "changed": bool(summary and summary["changed"]),
                            "cancelled": True,
                            "project": self._application.projects.get(request.project_id),
                        }
                    )
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


def check_desktop_environment():
    """Fail before native GUI initialization can abort a headless Linux process."""
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY")
        or os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("QT_QPA_PLATFORM") in {"offscreen", "minimal"}
    ):
        raise SystemExit(
            "Linux desktop mode needs an active graphical session (DISPLAY or WAYLAND_DISPLAY). "
            "Run from your desktop terminal. For a headless HTTP server use --browser. "
            "See docs/linux.md; an offscreen test is not a visible desktop window."
        )


def desktop_gui(gui: str | None = None) -> str | None:
    """Use the bundled Linux Qt binding without changing other OS defaults.

    A CLI override takes precedence over pywebview's supported Linux environment
    override. On Windows/macOS, pywebview keeps control of its native selection.
    """
    if gui is not None or not sys.platform.startswith("linux"):
        return gui
    override = os.environ.get("PYWEBVIEW_GUI", "").lower()
    return override if override in {"qt", "gtk"} else "qt"


def desktop_error(error: Exception, gui: str | None) -> str:
    message = f"Desktop WebView could not start: {error}. "
    if sys.platform.startswith("linux"):
        if gui == "gtk":
            return message + (
                "GTK was explicitly selected. Install GTK/WebKit2GTK and the matching Python "
                "bindings in this environment, or use uv run evograph --gui qt. See docs/linux.md."
            )
        return message + (
            "uv run evograph installs the Linux Qt Python backend automatically; "
            "run uv sync to repair an incomplete Python environment. Missing native graphics "
            "libraries must be installed for your distribution. See docs/linux.md."
        )
    if sys.platform == "win32":
        return message + "Check the Microsoft Edge WebView2 Runtime and run uv sync."
    if sys.platform == "darwin":
        return message + "Check the system WebKit/PyObjC backend and run uv sync."
    return message + "Check pywebview's supported platforms and installed native backend."


def launch(application: Application, dist: Path, gui: str | None = None):
    import webview

    check_desktop_environment()
    gui = desktop_gui(gui)
    if sys.platform.startswith("linux") and gui == "qt":
        # QtPy otherwise prefers an unrelated PyQt installation when one exists.
        # Keep an explicit user binding override, and never load Qt for browser mode.
        os.environ.setdefault("QT_API", "pyside6")
    configure_asset_types()
    index = dist / "index.html"
    if not index.exists():
        raise SystemExit("前端尚未构建。请先运行 npm ci 和 npm run build。")
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
    try:
        webview.start(http_server=True, gui=gui)
    except webview.errors.WebViewException as exc:
        raise SystemExit(desktop_error(exc, gui)) from exc
