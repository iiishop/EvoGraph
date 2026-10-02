"""Keep the built frontend in step with its sources.

The desktop and browser entry points both serve ``dist/``, so an edit under
``frontend/`` stays invisible until someone runs ``npm run build``. Start-up
rebuilds the bundle when a source is newer than the built output, unless the
caller passes ``--no-build``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

# Everything under these paths ends up inside the bundle.
WATCHED_DIRS = ("frontend/src",)
WATCHED_FILES = (
    "frontend/index.html",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
)

BUILD_TIMEOUT_SECONDS = 300


def _newest_mtime(paths: list[Path]) -> float:
    newest = 0.0
    for path in paths:
        if not path.exists():
            continue
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
            continue
        for item in path.rglob("*"):
            if item.is_file():
                newest = max(newest, item.stat().st_mtime)
    return newest


def bundle_time(dist: Path) -> float:
    """Newest mtime inside the built bundle, 0.0 when it is missing."""
    newest = 0.0
    if not dist.exists():
        return newest
    for item in dist.rglob("*"):
        if item.is_file():
            newest = max(newest, item.stat().st_mtime)
    return newest


def is_stale(project_root: Path, dist: Path) -> bool:
    """True when the bundle is missing or older than a frontend source."""
    if not (dist / "index.html").exists():
        return True
    sources = [project_root / name for name in (*WATCHED_DIRS, *WATCHED_FILES)]
    return _newest_mtime(sources) > bundle_time(dist)


def npm_command() -> list[str] | None:
    """``npm run build`` as an argv list, or None when npm is not installed.

    CreateProcess cannot launch ``npm.cmd`` directly, so on Windows the call
    goes through the command interpreter.
    """
    npm = shutil.which("npm")
    if npm is None:
        return None
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", npm, "run", "build"]
    return [npm, "run", "build"]


def _dependency_issues(project_root: Path) -> list[str]:
    """Check installed packages, including an old but present node_modules tree.

    uv only manages Python dependencies. Compare package metadata rather than
    directory timestamps, which cannot establish that npm dependencies match a
    newly checked-out lockfile. Missing optional packages are normal across OSes.
    """
    manifest_path = project_root / "package.json"
    if not manifest_path.is_file():
        return []  # Let npm explain a missing project manifest.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {
        f"node_modules/{name}"
        for group in ("dependencies", "devDependencies")
        for name in manifest.get(group, {})
    }
    lock_path = project_root / "package-lock.json"
    locked = {}
    if lock_path.is_file():
        locked = json.loads(lock_path.read_text(encoding="utf-8")).get("packages", {})
    installed_lock_path = project_root / "node_modules/.package-lock.json"
    installed_lock = {}
    if installed_lock_path.is_file():
        installed_lock = json.loads(installed_lock_path.read_text(encoding="utf-8")).get(
            "packages", {}
        )

    issues = []
    locked_manifest = locked.get("")
    if locked_manifest is not None and any(
        manifest.get(group, {}) != locked_manifest.get(group, {})
        for group in ("dependencies", "devDependencies")
    ):
        issues.append("package.json（与 package-lock.json 不一致，请先同步依赖清单和锁文件）")
    for name in sorted(required | locked.keys()):
        if not name.startswith("node_modules/") or ".." in Path(name).parts:
            continue
        expected = locked.get(name, {})
        if expected.get("link"):
            continue
        installed_path = project_root / name / "package.json"
        if not installed_path.is_file():
            if name in required or not expected.get("optional"):
                issues.append(f"{name.removeprefix('node_modules/')}（缺失）")
            continue
        installed = json.loads(installed_path.read_text(encoding="utf-8"))
        recorded = installed_lock.get(name, {})
        version = expected.get("version")
        if (version and installed.get("version") != version) or any(
            expected.get(field) and recorded.get(field) and recorded[field] != expected[field]
            for field in ("version", "integrity")
        ):
            issues.append(f"{name.removeprefix('node_modules/')}（与 package-lock.json 不一致）")
    return issues


def _build_output(output: str | bytes | None) -> str:
    # TimeoutExpired can contain bytes even when subprocess.run uses text=True.
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return (output or "").strip() or "（npm 未输出诊断信息）"


def rebuild(project_root: Path) -> tuple[bool, str]:
    """Run the frontend build. Returns ``(ok, message)``; never raises."""
    command = npm_command()
    if command is None:
        return False, "未找到 npm，跳过前端构建（沿用现有 dist）"
    install_command = "npm ci" if (project_root / "package-lock.json").is_file() else "npm install"
    try:
        issues = _dependency_issues(project_root)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return (
            False,
            f"前端依赖检查失败：{error}\n请在 {project_root} 运行 {install_command} 后重试",
        )
    if issues:
        details = "\n".join(issues)
        return False, (
            f"前端依赖缺失或已过期：\n{details}\n"
            f"uv 不会安装 npm 依赖。请在 {project_root} 运行 {install_command} 后重试"
        )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        return False, (
            f"前端构建超时（{BUILD_TIMEOUT_SECONDS}s），沿用现有 dist：\n"
            f"{_build_output(error.output)}"
        )
    except OSError as error:
        return False, f"前端构建无法启动：{error}"
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        return False, (
            f"前端构建失败（npm 退出码 {completed.returncode}）：\n"
            f"{_build_output(completed.stdout)}"
        )
    return True, f"前端构建完成（{elapsed:.1f}s）"
