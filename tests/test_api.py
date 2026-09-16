import asyncio

from evograph.transport.http import create_app
from fastapi.testclient import TestClient


def test_http_and_bridge_use_the_same_contract(app):
    from evograph.transport.desktop import DesktopBridge

    bridge = DesktopBridge(app)
    result = bridge.command("projects.create", {"name": "Bridge project"})
    assert result["ok"]
    client = TestClient(create_app(app))
    response = client.post("/api/command", json={"action": "projects.list"})
    assert response.json()["data"][0]["name"] == "Bridge project"


def test_reject_foreign_web_origins(app):
    client = TestClient(create_app(app))
    response = client.post(
        "/api/command", headers={"Origin": "https://evil.example"}, json={"action": "projects.list"}
    )
    assert response.status_code == 403


def test_production_javascript_mime_type(app, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "app.js").write_text("export const ok = true;")
    client = TestClient(create_app(app, dist))
    response = client.get("/app.js")
    assert response.headers["content-type"].startswith("text/javascript")


def test_provider_key_never_enters_sqlite_or_response(app):
    saved = app.settings.save(
        "openai_compatible",
        {"base_url": "https://example.com/v1", "model": "test"},
        "secret-sentinel",
    )
    assert "secret-sentinel" not in str(saved)
    assert b"secret-sentinel" not in app.db.path.read_bytes()
    assert saved["provider"]["has_key"]
    # Changing endpoint cannot forward the key to a new server.
    changed = app.settings.save(
        "openai_compatible", {"base_url": "https://other.example/v1", "model": "test"}
    )
    assert not changed["provider"]["has_key"]


def test_type_validation_rejects_wrong_command_shape(app):
    result = asyncio.run(
        app.dispatch(
            "milestone.verify", {"project_id": "p", "milestone_id": "M1", "command": "rm -rf"}
        )
    )
    assert not result["ok"] and result["error"]["code"] == "VALIDATION"


def test_project_operation_lock_rejects_overlapping_mutations(app):
    import threading

    app._locks["project"] = threading.Lock()
    app._locks["project"].acquire()
    try:
        result = asyncio.run(
            app.dispatch("milestone.start", {"project_id": "project", "milestone_id": "M1"})
        )
        assert result["error"]["code"] == "BUSY"
    finally:
        app._locks["project"].release()
