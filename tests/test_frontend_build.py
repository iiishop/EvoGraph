import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from evograph import __main__ as launcher
from evograph.frontend_build import (
    MANIFEST_NAME,
    WATCHED_PATHS,
    bundle_status,
    file_hashes,
    is_stale,
    npm_command,
    rebuild,
    source_hash,
    source_hashes,
)


def write_at(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (when, when))


def record_bundle(project: Path) -> None:
    dist = project / "dist"
    dist.mkdir(exist_ok=True)
    if not (dist / "index.html").exists():
        (dist / "index.html").write_text("built frontend", encoding="utf-8")
    sources = source_hashes(project)
    write_json(dist / MANIFEST_NAME, {
        "schema": 1, "version": "0.1.0", "builtAt": "2026-10-03T00:00:00Z",
        "sourceHash": source_hash(sources), "sources": sources,
        "outputs": file_hashes(dist, (".",), MANIFEST_NAME),
    })


def build_fixture(tmp_path: Path) -> tuple[Path, Path, float]:
    now = time.time()
    write_at(tmp_path / "frontend/src/main.ts", now - 100)
    write_at(tmp_path / "frontend/index.html", now - 100)
    write_at(tmp_path / "vite.config.ts", now - 100)
    dist = tmp_path / "dist"
    write_at(dist / "index.html", now - 50)
    record_bundle(tmp_path)
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
        record_bundle(kwargs["cwd"])
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
def test_launcher_dependency_failure_blocks_even_with_existing_bundle(
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

    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert "npm ci\nnpm run build" in str(error.value)
    assert "--no-build" in str(error.value)
    assert launched == []
    if has_bundle:
        assert (dist / "index.html").read_text(encoding="utf-8") == "existing bundle"
    assert "@tiptap/core（缺失）" in capsys.readouterr().out
    assert saved_state.read_bytes() == b"existing project state"
    assert build_runner == []


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_browser_cli_never_starts_or_checks_desktop(tmp_path, monkeypatch, platform):
    from evograph.transport import desktop

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "argv", ["evograph", "--browser", "--no-build"])
    monkeypatch.setattr(launcher, "Application", lambda _: "application")
    monkeypatch.setattr("evograph.transport.http.create_app", lambda app, dist: app)
    def forbidden(*args, **kwargs):
        pytest.fail("browser mode touched the desktop backend")
    monkeypatch.setattr(desktop, "check_desktop_environment", forbidden)
    monkeypatch.setattr(desktop, "launch", forbidden)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))
    launcher.main()
    assert calls == [("application", {"host": "127.0.0.1", "port": 8765, "ws": "none"})]


def test_conflicting_cli_flags_fail_before_build_or_state(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evograph", "--browser", "--gui", "qt", "--build"])
    def forbidden(*args, **kwargs):
        pytest.fail("invalid arguments triggered work")
    monkeypatch.setattr(launcher, "rebuild", forbidden)
    monkeypatch.setattr(launcher, "Application", forbidden)
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2
    assert "--gui cannot be combined with --browser" in capsys.readouterr().err


@pytest.mark.parametrize("name", [
    "frontend/src/main.ts", "frontend/index.html", "frontend/public/logo.svg",
    "package-lock.json", "package.json", "tsconfig.json", "vite.config.ts",
    "tools/frontend_provenance.mjs", "tools/frontend_provenance.d.mts",
])
def test_content_change_is_stale_even_with_preserved_or_older_timestamp(tmp_path, name):
    project, dist, now = build_fixture(tmp_path)
    target = project / name
    write_at(target, now - 100)
    record_bundle(project)
    target.write_text("changed but older", encoding="utf-8")
    os.utime(target, (now - 1000, now - 1000))
    assert is_stale(project, dist)


def test_timestamp_only_changes_do_not_rebuild(tmp_path):
    project, dist, now = build_fixture(tmp_path)
    os.utime(project / "frontend/src/main.ts", (now + 1000, now + 1000))
    assert not is_stale(project, dist)


def test_deleted_source_is_stale(tmp_path):
    project, dist, _ = build_fixture(tmp_path)
    (project / "frontend/src/main.ts").unlink()
    assert is_stale(project, dist)


def test_copied_bundle_is_portable_but_other_checkout_content_is_stale(tmp_path):
    original, _, _ = build_fixture(tmp_path / "original")
    copied = tmp_path / "copied"
    shutil.copytree(original, copied)
    assert not is_stale(copied, copied / "dist")
    source = copied / "frontend/src/main.ts"
    stamp = source.stat().st_mtime
    source.write_text("other checkout", encoding="utf-8")
    os.utime(source, (stamp, stamp))
    assert is_stale(copied, copied / "dist")


def test_linked_bundle_is_compared_with_current_checkout(tmp_path):
    original, _, _ = build_fixture(tmp_path / "original")
    copied = tmp_path / "copied"
    shutil.copytree(original, copied, ignore=shutil.ignore_patterns("dist"))
    try:
        (copied / "dist").symlink_to(original / "dist", target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this platform")
    assert not is_stale(copied, copied / "dist")
    (copied / "frontend/src/main.ts").write_text("different checkout", encoding="utf-8")
    assert is_stale(copied, copied / "dist")


@pytest.mark.parametrize("change", ["delete", "modify", "add"])
def test_incomplete_or_replaced_output_is_stale(tmp_path, change):
    project, dist, now = build_fixture(tmp_path)
    asset = dist / "assets/main.js"
    write_at(asset, now)
    record_bundle(project)
    if change == "delete":
        asset.unlink()
    elif change == "modify":
        asset.write_text("foreign output", encoding="utf-8")
        os.utime(asset, (now, now))
    else:
        write_at(dist / "assets/foreign.js", now)
    assert is_stale(project, dist)


@pytest.mark.parametrize("manifest", [None, "{", "[]", '{"schema": 999}'])
def test_legacy_or_invalid_manifest_never_claims_freshness(tmp_path, manifest):
    project, dist, _ = build_fixture(tmp_path)
    path = dist / MANIFEST_NAME
    if manifest is None:
        path.unlink()
    else:
        path.write_text(manifest, encoding="utf-8")
    assert is_stale(project, dist)


def test_source_free_prebuilt_distribution_needs_no_node_or_npm(tmp_path):
    project, dist, _ = build_fixture(tmp_path / "source")
    packaged = tmp_path / "package"
    shutil.copytree(dist, packaged / "dist")
    valid, description = bundle_status(packaged, packaged / "dist")
    assert valid
    assert "v0.1.0" in description
    assert "无本地源码" in description


def test_successful_command_without_verified_output_is_a_build_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("evograph.frontend_build.npm_command", lambda: ["npm", "run", "build"])
    monkeypatch.setattr("evograph.frontend_build.subprocess.run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, stdout="ok"))
    ok, message = rebuild(tmp_path)
    assert not ok
    assert "命令成功" in message and "校验失败" in message


def mock_browser_launcher(project, monkeypatch, flags=()):
    monkeypatch.setattr(launcher, "__file__", str(project / "backend/evograph/__main__.py"))
    monkeypatch.setattr(sys, "argv", ["evograph", "--browser", *flags])
    launched = []
    monkeypatch.setattr(launcher, "Application", lambda _: "application")
    monkeypatch.setattr("evograph.transport.http.create_app", lambda app, bundle: (app, bundle))
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: launched.append(app))
    return launched


def test_verified_offline_checkout_skips_dependency_and_npm_checks(tmp_path, monkeypatch, capsys):
    project, dist, _ = build_fixture(tmp_path)
    launched = mock_browser_launcher(project, monkeypatch)
    monkeypatch.setattr(launcher, "rebuild", lambda *_: pytest.fail("unnecessary rebuild"))
    launcher.main()
    assert launched == [("application", dist)]
    assert "已校验" in capsys.readouterr().out


@pytest.mark.parametrize("flags", [(), ("--build",)])
def test_failed_auto_or_forced_build_cannot_launch_old_interface(tmp_path, monkeypatch, flags):
    project, dist, _ = build_fixture(tmp_path)
    (project / "frontend/src/main.ts").write_text("new version", encoding="utf-8")
    launched = mock_browser_launcher(project, monkeypatch, flags)
    monkeypatch.setattr(launcher, "rebuild", lambda *_: (False, "compiler error TS2307"))
    with pytest.raises(SystemExit, match="停止启动"):
        launcher.main()
    assert launched == []
    assert (dist / "index.html").is_file()


@pytest.mark.parametrize("has_bundle", [True, False])
def test_no_build_is_an_explicit_warned_override_and_keeps_browser_api_mode(
    tmp_path, monkeypatch, capsys, has_bundle
):
    project = tmp_path
    if has_bundle:
        project, _, _ = build_fixture(tmp_path)
        (project / "frontend/src/main.ts").write_text("new version", encoding="utf-8")
    launched = mock_browser_launcher(project, monkeypatch, ["--no-build"])
    monkeypatch.setattr(launcher, "rebuild", lambda *_: pytest.fail("must skip build"))
    monkeypatch.setattr(launcher, "bundle_status", lambda *_: pytest.fail("must skip validation"))
    launcher.main()
    assert launched == [("application", project / "dist")]
    assert "--no-build" in capsys.readouterr().out


def test_node_and_python_fingerprints_match(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node is only needed for the cross-language build contract")
    project, _, _ = build_fixture(tmp_path)
    for name in WATCHED_PATHS:
        if name != "frontend":
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture: {name}", encoding="utf-8")
    (project / "frontend/src/界面😀.ts").write_text("multiplatform\n", encoding="utf-8")
    tool = Path(__file__).resolve().parents[1] / "tools/frontend_provenance.mjs"
    script = (
        f"import {{sourceHashes, sourceHash}} from {json.dumps(tool.as_uri())};"
        f"const files = sourceHashes({json.dumps(str(project))});"
        "process.stdout.write(JSON.stringify({files, hash: sourceHash(files)}));"
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            capture_output=True, text=True, check=True)
    node = json.loads(result.stdout)
    assert node["files"] == source_hashes(project)
    assert node["hash"] == source_hash(source_hashes(project))


def test_rebuild_never_overwrites_a_linked_bundle(tmp_path, build_runner):
    original, _, _ = build_fixture(tmp_path / "original")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    try:
        (checkout / "dist").symlink_to(original / "dist", target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this platform")
    original_outputs = file_hashes(original / "dist", (".",))
    ok, message = rebuild(checkout)
    assert not ok and "符号链接" in message
    assert build_runner == []
    assert file_hashes(original / "dist", (".",)) == original_outputs


def test_windows_junction_cannot_be_overwritten(tmp_path, monkeypatch, build_runner):
    # Directory junctions have a mount-point reparse tag, but are not S_IFLNK.
    import stat

    project, dist, _ = build_fixture(tmp_path)
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == dist:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    ok, message = rebuild(project)
    assert not ok and "Windows 目录联接" in message
    assert build_runner == []


def test_nested_windows_junction_is_not_hashed(tmp_path, monkeypatch):
    import stat

    project, dist, _ = build_fixture(tmp_path)
    linked = project / "frontend/src"
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == linked:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert is_stale(project, dist)


def test_launcher_binds_build_to_inputs_before_npm_and_rejects_concurrent_edits(
    tmp_path, monkeypatch
):
    from evograph.frontend_build import EXPECTED_SOURCE_HASH_ENV

    project, _, _ = build_fixture(tmp_path)
    expected = source_hash(source_hashes(project))
    monkeypatch.setattr("evograph.frontend_build.npm_command", lambda: ["npm", "run", "build"])

    def run(command, **kwargs):
        assert kwargs["env"][EXPECTED_SOURCE_HASH_ENV] == expected
        (project / "vite.config.ts").write_text("new config during npm", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="built")

    monkeypatch.setattr("evograph.frontend_build.subprocess.run", run)
    ok, message = rebuild(project)
    assert not ok and "构建期间发生变化" in message
    assert is_stale(project, project / "dist")
