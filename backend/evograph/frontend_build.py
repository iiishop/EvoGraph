"""Keep the built frontend in step with its sources.

The desktop and browser entry points both serve ``dist/``, so an edit under
``frontend/`` stays invisible until someone runs ``npm run build``. Start-up
rebuilds the bundle when a source is newer than the built output, unless the
caller passes ``--no-build``.
"""

from __future__ import annotations

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


def rebuild(project_root: Path) -> tuple[bool, str]:
    """Run the frontend build. Returns ``(ok, message)``; never raises."""
    command = npm_command()
    if command is None:
        return False, "未找到 npm，跳过前端构建（沿用现有 dist）"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"前端构建超时（{BUILD_TIMEOUT_SECONDS}s），沿用现有 dist"
    except OSError as error:
        return False, f"前端构建无法启动：{error}"
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "").strip().splitlines()
        tail = "\n".join(output[-15:])
        return False, f"前端构建失败（npm 退出码 {completed.returncode}）：\n{tail}"
    return True, f"前端构建完成（{elapsed:.1f}s）"
