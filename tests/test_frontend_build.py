import os
import time
from pathlib import Path

from evograph.frontend_build import is_stale, npm_command, rebuild


def write_at(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (when, when))


def build_fixture(tmp_path: Path) -> tuple[Path, Path, float]:
    now = time.time()
    write_at(tmp_path / "frontend/src/main.ts", now - 100)
    write_at(tmp_path / "frontend/index.html", now - 100)
    write_at(tmp_path / "vite.config.ts", now - 100)
    dist = tmp_path / "dist"
    write_at(dist / "index.html", now - 50)
    return tmp_path, dist, now


def test_missing_bundle_is_stale(tmp_path):
    assert is_stale(tmp_path, tmp_path / "dist")


def test_fresh_bundle_is_not_stale(tmp_path):
    project, dist, _ = build_fixture(tmp_path)
    assert not is_stale(project, dist)


def test_edited_source_makes_bundle_stale(tmp_path):
    project, dist, now = build_fixture(tmp_path)
    write_at(project / "frontend/src/styles/dialogs.css", now)
    assert is_stale(project, dist)


def test_dependency_change_makes_bundle_stale(tmp_path):
    project, dist, now = build_fixture(tmp_path)
    write_at(project / "package-lock.json", now)
    assert is_stale(project, dist)


def test_npm_command_is_none_without_npm(monkeypatch):
    monkeypatch.setattr("evograph.frontend_build.shutil.which", lambda name: None)
    assert npm_command() is None
    ok, message = rebuild(Path.cwd())
    assert ok is False
    assert "npm" in message


def test_rebuild_reports_failure_without_raising(tmp_path):
    ok, message = rebuild(tmp_path)
    assert ok is False
    assert "npm" in message or "构建" in message
