import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from evograph import __main__ as launcher
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


def write_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding="utf-8")


def dependency_fixture(tmp_path):
    manifest = {"dependencies": {"vue": "^3.5.0", "@tiptap/core": "3.31.4"}}
    packages = {
        "": manifest,
        "node_modules/vue": {"version": "3.5.42", "integrity": "vue-current"},
        "node_modules/@tiptap/core": {"version": "3.31.4", "integrity": "tiptap-current"},
        "node_modules/transitive": {"version": "1.0.0", "integrity": "transitive-current"},
    }
    write_json(tmp_path / "package.json", manifest)
    write_json(tmp_path / "package-lock.json", {"lockfileVersion": 3, "packages": packages})
    install_fixture(tmp_path, packages)
    return packages


def install_fixture(tmp_path, packages):
    for name, metadata in packages.items():
        if name:
            write_json(tmp_path / name / "package.json", {"version": metadata["version"]})
    write_json(
        tmp_path / "node_modules/.package-lock.json",
        {"lockfileVersion": 3, "packages": {k: v for k, v in packages.items() if k}},
    )


@pytest.fixture
def build_runner(monkeypatch):
    calls = []
    monkeypatch.setattr("evograph.frontend_build.npm_command", lambda: ["npm", "run", "build"])

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="build succeeded")

    monkeypatch.setattr("evograph.frontend_build.subprocess.run", run)
    return calls


def test_missing_new_dependencies_detected_in_old_node_modules(tmp_path, build_runner):
    dependency_fixture(tmp_path)
    (tmp_path / "node_modules/@tiptap/core/package.json").unlink()
    assert (tmp_path / "node_modules").is_dir()

    ok, message = rebuild(tmp_path)

    assert not ok
    assert "@tiptap/core（缺失）" in message
    assert "npm ci" in message
    assert str(tmp_path) in message
    assert build_runner == []


def test_missing_entire_install_detected(tmp_path, build_runner):
    write_json(tmp_path / "package.json", {"devDependencies": {"vue-tsc": "2.2.12"}})
    ok, message = rebuild(tmp_path)
    assert not ok
    assert "vue-tsc（缺失）" in message
    assert "npm install" in message
    assert build_runner == []


def test_locked_direct_dependency_version_detected(tmp_path, build_runner):
    dependency_fixture(tmp_path)
    write_json(tmp_path / "node_modules/@tiptap/core/package.json", {"version": "3.30.0"})
    ok, message = rebuild(tmp_path)
    assert not ok
    assert "@tiptap/core（与 package-lock.json 不一致）" in message
    assert build_runner == []


def test_lock_change_requires_install_and_retries_after_sync(tmp_path, build_runner):
    packages = dependency_fixture(tmp_path)
    packages["node_modules/transitive"] = {"version": "1.1.0", "integrity": "new-transitive"}
    write_json(tmp_path / "package-lock.json", {"lockfileVersion": 3, "packages": packages})

    ok, message = rebuild(tmp_path)
    assert not ok
    assert "transitive（与 package-lock.json 不一致）" in message
    assert build_runner == []

    install_fixture(tmp_path, packages)
    ok, message = rebuild(tmp_path)
    assert ok
    assert "前端构建完成" in message
    assert len(build_runner) == 1


def test_hidden_lock_integrity_change_detected(tmp_path, build_runner):
    packages = dependency_fixture(tmp_path)
    packages["node_modules/transitive"]["integrity"] = "changed-content-same-version"
    write_json(tmp_path / "package-lock.json", {"lockfileVersion": 3, "packages": packages})
    ok, message = rebuild(tmp_path)
    assert not ok
    assert "transitive" in message
    assert build_runner == []


def test_hidden_lock_can_omit_integrity_metadata(tmp_path, build_runner):
    packages = dependency_fixture(tmp_path)
    write_json(
        tmp_path / "node_modules/.package-lock.json",
        {"packages": {k: {"version": v["version"]} for k, v in packages.items() if k}},
    )
    assert rebuild(tmp_path)[0]
    assert len(build_runner) == 1


@pytest.mark.parametrize("hidden_lock", [True, False])
def test_fresh_install_builds_without_optional_platform_packages(
    tmp_path, build_runner, hidden_lock
):
    packages = dependency_fixture(tmp_path)
    packages["node_modules/@rollup/rollup-win32-x64-msvc"] = {
        "version": "4.60.1",
        "optional": True,
        "os": ["win32"],
        "cpu": ["x64"],
    }
    packages["node_modules/@rollup/rollup-linux-x64-gnu"] = {
        "version": "4.60.1",
        "optional": True,
        "os": ["linux"],
        "cpu": ["x64"],
    }
    write_json(tmp_path / "package-lock.json", {"lockfileVersion": 3, "packages": packages})
    if hidden_lock:
        # npm versions may record different non-identity metadata in the hidden lock.
        hidden = {k: {**v, "dev": True, "license": "MIT"} for k, v in packages.items() if k}
        write_json(tmp_path / "node_modules/.package-lock.json", {"packages": hidden})
    else:
        (tmp_path / "node_modules/.package-lock.json").unlink()

    ok, _ = rebuild(tmp_path)
    assert ok
    _, kwargs = build_runner[0]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT


def test_malformed_installed_metadata_is_actionable(tmp_path, build_runner):
    dependency_fixture(tmp_path)
    (tmp_path / "node_modules/@tiptap/core/package.json").write_text("{", encoding="utf-8")
    ok, message = rebuild(tmp_path)
    assert not ok
    assert "前端依赖检查失败" in message
    assert "npm ci" in message
    assert build_runner == []


def test_failed_build_reports_both_streams_without_truncation(tmp_path, monkeypatch):
    # Exercise actual pipe handling: npm warnings must not hide vue-tsc stdout.
    script = (
        "import sys; "
        "print('npm warn Unknown user config electron_mirror', file=sys.stderr, flush=True); "
        "[print(f'TS2307 missing dependency {i}', flush=True) for i in range(40)]; "
        "sys.exit(2)"
    )
    monkeypatch.setattr(
        "evograph.frontend_build.npm_command", lambda: [sys.executable, "-c", script]
    )
    for _ in range(2):  # A failed build must not mark a subsequent attempt as fresh.
        ok, message = rebuild(tmp_path)
        assert not ok
        assert "npm 退出码 2" in message
        assert "npm warn Unknown user config electron_mirror" in message
        assert "TS2307 missing dependency 0" in message
        assert "TS2307 missing dependency 39" in message


def test_timeout_keeps_partial_diagnostics(tmp_path, monkeypatch, build_runner):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"partial build output")

    monkeypatch.setattr("evograph.frontend_build.subprocess.run", timeout)
    ok, message = rebuild(tmp_path)
    assert not ok
    assert "超时" in message
    assert "partial build output" in message


def test_windows_npm_command_preserves_paths_with_spaces(monkeypatch):
    npm = r"C:\Program Files\nodejs\npm.cmd"
    comspec = r"C:\Windows\System32\cmd.exe"
    monkeypatch.setattr("evograph.frontend_build.shutil.which", lambda name: npm)
    # Replace this module's os binding so pathlib/pytest keep their real platform.
    monkeypatch.setattr(
        "evograph.frontend_build.os", SimpleNamespace(name="nt", environ={"COMSPEC": comspec})
    )
    command = npm_command()
    assert command == [comspec, "/c", npm, "run", "build"]
    assert subprocess.list2cmdline(command) == f'{comspec} /c "{npm}" run build'


@pytest.mark.parametrize("has_bundle", [False, True])
def test_launcher_dependency_failure_keeps_existing_bundle(
    tmp_path, monkeypatch, build_runner, capsys, has_bundle
):
    dependency_fixture(tmp_path)
    (tmp_path / "node_modules/@tiptap/core/package.json").unlink()
    dist = tmp_path / "dist"
    if has_bundle:
        dist.mkdir()
        (dist / "index.html").write_text("existing bundle", encoding="utf-8")
    saved_state = tmp_path / "user-data/evograph.sqlite3"
    saved_state.parent.mkdir()
    saved_state.write_bytes(b"existing project state")
    monkeypatch.setattr(launcher, "__file__", str(tmp_path / "backend/evograph/__main__.py"))
    monkeypatch.setattr(sys, "argv", ["evograph", "--browser", "--build"])
    monkeypatch.setattr(launcher, "Application", lambda _: "application")
    monkeypatch.setattr("evograph.transport.http.create_app", lambda app, bundle: (app, bundle))
    launched = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: launched.append(app))

    if has_bundle:
        launcher.main()
        assert launched == [("application", dist)]
        assert (dist / "index.html").read_text(encoding="utf-8") == "existing bundle"
    else:
        with pytest.raises(SystemExit) as error:
            launcher.main()
        assert "npm ci\nnpm run build" in str(error.value)
        assert launched == []
    assert "@tiptap/core（缺失）" in capsys.readouterr().out
    assert saved_state.read_bytes() == b"existing project state"
    assert build_runner == []
