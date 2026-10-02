import sys
import tomllib
from pathlib import Path
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
    monkeypatch.setattr(desktop.sys, "platform", "linux")
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
    with pytest.raises(SystemExit, match="uv run evograph"):
        desktop.launch(app, tmp_path)


@pytest.mark.parametrize("platform,expected", [("linux", "qt"), ("win32", None), ("darwin", None)])
def test_default_backend_matches_platform(monkeypatch, platform, expected):
    monkeypatch.setattr(desktop.sys, "platform", platform)
    monkeypatch.delenv("PYWEBVIEW_GUI", raising=False)
    assert desktop.desktop_gui() == expected


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
@pytest.mark.parametrize("gui", ["qt", "gtk"])
def test_explicit_gui_wins_on_each_platform(monkeypatch, platform, gui):
    monkeypatch.setattr(desktop.sys, "platform", platform)
    monkeypatch.setenv("PYWEBVIEW_GUI", "gtk" if gui == "qt" else "qt")
    assert desktop.desktop_gui(gui) == gui


@pytest.mark.parametrize("override,expected", [("GTK", "gtk"), ("qt", "qt"), ("bogus", "qt")])
def test_linux_honors_supported_environment_override(monkeypatch, override, expected):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setenv("PYWEBVIEW_GUI", override)
    assert desktop.desktop_gui() == expected


@pytest.mark.parametrize(
    "platform,gui,binding,expected",
    [("linux", None, None, "pyside6"), ("linux", None, "pyqt6", "pyqt6"),
     ("linux", "gtk", None, None), ("win32", None, None, None),
     ("darwin", None, None, None)],
)
def test_launch_sets_only_linux_qt_default_binding(
    app, tmp_path, monkeypatch, platform, gui, binding, expected
):
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setattr(desktop.sys, "platform", platform)
    monkeypatch.delenv("PYWEBVIEW_GUI", raising=False)
    monkeypatch.delenv("QT_API", raising=False)
    if binding:
        monkeypatch.setenv("QT_API", binding)
    calls = []
    fake = SimpleNamespace(
        create_window=lambda *a, **kw: object(),
        start=lambda **kw: calls.append(kw),
        errors=SimpleNamespace(WebViewException=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(desktop, "check_desktop_environment", lambda: None)
    desktop.launch(app, tmp_path, gui=gui)
    assert desktop.os.environ.get("QT_API") == expected
    assert calls == [{"http_server": True, "gui": desktop.desktop_gui(gui)}]


@pytest.mark.parametrize(
    "platform,gui,expected",
    [("linux", "qt", "automatically"), ("linux", "gtk", "GTK was explicitly selected"),
     ("win32", None, "WebView2"), ("darwin", None, "WebKit/PyObjC")],
)
def test_backend_error_is_platform_specific(monkeypatch, platform, gui, expected):
    monkeypatch.setattr(desktop.sys, "platform", platform)
    message = desktop.desktop_error(RuntimeError("native missing"), gui)
    assert expected in message
    assert "native missing" in message
    assert "--extra linux" not in message
    if platform != "linux":
        assert "Linux" not in message


def test_platform_dependency_markers_match_lock_and_preserve_legacy_extra():
    from packaging.requirements import Requirement

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    deps = [Requirement(value) for value in project["dependencies"]]
    for platform in ("linux", "win32", "darwin"):
        active = [dep for dep in deps if dep.marker is None or dep.marker.evaluate({"sys_platform": platform})]
        assert any(dep.name == "pywebview" for dep in active)
        assert any("pyside6" in dep.extras for dep in active) == (platform == "linux")
    assert project["optional-dependencies"]["linux"] == []
    lock = tomllib.loads((root / "uv.lock").read_text())
    package = next(item for item in lock["package"] if item["name"] == "evograph")
    qt = next(dep for dep in package["dependencies"] if "pyside6" in dep.get("extra", []))
    assert qt["marker"] == "sys_platform == 'linux'"
