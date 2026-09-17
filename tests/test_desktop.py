import asyncio
import threading

from evograph.transport.desktop import DesktopBridge


def test_desktop_bridge_delivers_terminal_event(app, planned):
    delivered = threading.Event()
    scripts = []

    class Window:
        def evaluate_js(self, script):
            scripts.append(script)
            if '"type": "done"' in script:
                delivered.set()

    async def stream(**params):
        yield {"type": "started"}
        await asyncio.sleep(0)
        yield {"type": "done", "changed": False}

    app.agent.stream = stream
    bridge = DesktopBridge(app)
    bridge._window = Window()
    assert bridge.start_agent("request", {"project_id": planned.id, "content": "test"})["started"]
    assert delivered.wait(3)
    assert all("evograph:agent" in script and "request" in script for script in scripts)
