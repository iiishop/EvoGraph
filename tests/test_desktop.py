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


def test_desktop_cancellation_delivers_snapshot_after_partial_changes_are_saved(app, planned):
    import json

    started = threading.Event()
    delivered = threading.Event()
    events = []

    class Window:
        def evaluate_js(self, script):
            payload = script.split("{detail: ", 1)[1].rsplit("}))", 1)[0]
            event = json.loads(payload)["event"]
            events.append(event)
            if event["type"] == "started":
                started.set()
            if event["type"] == "done":
                delivered.set()

    async def stream(**params):
        yield {"type": "started", "turn_id": "cancelled-turn"}
        try:
            await asyncio.sleep(30)
        finally:
            p = app.db.get(planned.id)
            p.milestones[0].title = "Committed before stop completed"
            app.db.save(p, "test_partial_change")

    app.agent.stream = stream
    app.agent.turn_result = lambda project_id, turn_id: {
        "turn_id": turn_id,
        "pending": False,
        "summary": {"turn_id": turn_id, "status": "stopped", "changed": True},
    }
    bridge = DesktopBridge(app)
    bridge._window = Window()
    bridge.start_agent("cancel-request", {"project_id": planned.id, "content": "test"})
    assert started.wait(3)
    bridge.cancel_agent("cancel-request")
    assert delivered.wait(3)
    final = events[-1]
    assert final["type"] == "done" and final["cancelled"]
    assert final["summary"]["status"] == "stopped"
    assert final["project"]["milestones"][0]["title"] == "Committed before stop completed"
    assert final["project"]["revision"] == app.db.get(planned.id).revision


def test_desktop_stream_forwards_compact_negotiation_on_one_admission(app, planned):
    delivered = threading.Event()
    requests = []

    class Window:
        def evaluate_js(self, script):
            if '"type": "done"' in script:
                delivered.set()

    async def stream(**params):
        requests.append(params)
        yield {"type": "done", "changed": False}

    app.agent.stream = stream
    bridge = DesktopBridge(app)
    bridge._window = Window()
    bridge.start_agent(
        "compact", {"project_id": planned.id, "content": "test", "snapshot_mode": "compact-v1"}
    )
    assert delivered.wait(3)
    assert len(requests) == 1 and requests[0]["snapshot_mode"] == "compact-v1"
