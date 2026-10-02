import asyncio
import json
import threading

import pytest


def dispatch(app, action, **params):
    return asyncio.run(app.dispatch(action, params))


def test_search_and_vision_saves_touch_only_their_own_setting(app):
    app.research.configure_search("tavily", {}, "synthetic-search-key")
    before = app.research.settings()["saved"]
    result = dispatch(app, "research.configure_vision", vision_enabled=True)
    assert result["ok"]
    assert result["data"]["saved"] == before
    assert result["data"]["vision_enabled"] is True
    assert list(app.research.secrets.values.values()) == ["synthetic-search-key"]

    result = dispatch(app, "research.configure_search", provider="bing", config={})
    assert result["ok"]
    assert result["data"]["vision_enabled"] is True
    assert result["data"]["saved"]["provider"] == "bing"
    result = dispatch(app, "research.configure_search", provider="")
    assert result["data"]["saved"] is None
    assert result["data"]["vision_enabled"] is True


def test_legacy_combined_operation_is_still_compatible(app):
    result = dispatch(
        app, "research.configure", provider="tavily", api_key="synthetic", vision_enabled=True
    )
    assert result["ok"]
    assert result["data"]["saved"]["has_key"]
    assert result["data"]["vision_enabled"] is True
    result = dispatch(app, "research.configure")
    assert result["data"]["saved"] is None
    assert result["data"]["vision_enabled"] is False


def test_scoped_search_key_stays_out_of_database_and_response(app):
    result = dispatch(app, "research.configure_search", provider="tavily", api_key="SYNTHETIC-ONLY")
    assert result["ok"]
    assert "SYNTHETIC-ONLY" not in json.dumps(result)
    assert b"SYNTHETIC-ONLY" not in app.db.path.read_bytes()
    saved = result["data"]["saved"]
    result = dispatch(app, "research.configure_search", provider="tavily")
    assert result["data"]["saved"]["has_key"]
    assert result["data"]["saved"]["secret_id"] == saved["secret_id"]
    result = dispatch(app, "research.configure_search", provider="tavily", clear_key=True)
    assert not result["data"]["saved"]["has_key"]
    assert app.research.secrets.values[saved["secret_id"]] == ""


def test_changed_search_endpoint_does_not_inherit_a_key(app):
    app.research.configure_search("searxng", {"base_url": "http://127.0.0.1:8001"}, "synthetic")
    assert app.research.settings()["saved"]["has_key"]
    changed = app.research.configure_search("searxng", {"base_url": "http://127.0.0.1:8002"})
    assert not changed["saved"]["has_key"]


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("research.configure_vision", {"vision_enabled": "yes"}),
        ("research.configure_vision", {}),
        ("research.configure_vision", {"vision_enabled": True, "provider": "bing"}),
        ("research.configure_search", {"provider": "bing", "vision_enabled": False}),
        ("research.configure_search", {"provider": 42}),
        ("research.configure_search", {"provider": "missing"}),
        ("research.configure_search", {"provider": "searxng", "config": {"base_url": ""}}),
    ],
)
def test_invalid_or_cross_section_payloads_do_not_change_settings(app, action, params):
    app.research.configure("tavily", {}, "synthetic", vision_enabled=True)
    before = app.research.settings()
    result = dispatch(app, action, **params)
    assert not result["ok"]
    assert app.research.settings() == before


def test_read_settings_does_not_require_global_mutation_lock(app):
    app._locks["__global__"] = threading.Lock()
    app._locks["__global__"].acquire()
    try:
        assert dispatch(app, "research.settings")["ok"]
        result = dispatch(app, "research.configure_vision", vision_enabled=True)
        assert result["error"]["code"] == "BUSY"
        assert app.research.settings()["vision_enabled"] is False
    finally:
        app._locks["__global__"].release()
