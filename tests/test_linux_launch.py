import sys
from types import SimpleNamespace

import pytest
from evograph.transport import desktop


def test_headless_linux_has_actionable_error(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="graphical session"):
        desktop.check_desktop_environment()


@pytest.mark.parametrize(
    "key,value",
    [("DISPLAY", ":0"), ("WAYLAND_DISPLAY", "wayland-0"), ("QT_QPA_PLATFORM", "offscreen")],
)
def test_linux_display_and_explicit_test_platforms(monkeypatch, key, value):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setenv(key, value)
    desktop.check_desktop_environment()


def test_other_platforms_do_not_require_linux_display(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    desktop.check_desktop_environment()


def test_launch_passes_gui_to_webview(app, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html></html>")
    calls = {}
    window = object()

    def create_window(*args, **kwargs):
        calls["window"] = (args, kwargs)
        return window

    def start(**kwargs):
        calls["start"] = kwargs

    fake = SimpleNamespace(
        create_window=create_window,
        start=start,
        errors=SimpleNamespace(WebViewException=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "check_desktop_environment", lambda: None)
    desktop.launch(app, tmp_path, gui="qt")
    assert calls["start"] == {"http_server": True, "gui": "qt"}
    assert calls["window"][1]["js_api"]._window is window


def test_launch_reports_missing_native_backend(app, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html></html>")

    def fail(**kwargs):
        raise RuntimeError("You must have either QT or GTK")

    fake = SimpleNamespace(
        create_window=lambda *a, **kw: object(),
        start=fail,
        errors=SimpleNamespace(WebViewException=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "check_desktop_environment", lambda: None)
    with pytest.raises(SystemExit, match="uv sync --extra linux"):
        desktop.launch(app, tmp_path)
