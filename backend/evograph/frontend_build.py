"""Keep the built frontend in step with its sources.

The desktop and browser entry points both serve ``dist/``, so an edit under
``frontend/`` stays invisible until someone runs ``npm run build``. Start-up
verifies content hashes of the build inputs and outputs, rebuilding any stale
or unverifiable bundle unless the caller explicitly passes ``--no-build``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

# Match tools/frontend_provenance.mjs (verified by a cross-language contract test).
WATCHED_PATHS = (
    "frontend",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
    "tools/check_frontend_dependencies.mjs",
    "tools/frontend_provenance.mjs",
    "tools/frontend_provenance.d.mts",
)
MANIFEST_NAME = ".evograph-build.json"
EXPECTED_SOURCE_HASH_ENV = "EVOGRAPH_EXPECTED_FRONTEND_SOURCE_HASH"
BUILD_TIMEOUT_SECONDS = 300


def _is_link(path: Path) -> bool:
    """Include Windows junctions, which Path.is_symlink omits (also on Python 3.11)."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_reparse_tag", 0) == 0xA0000003


def file_hashes(root: Path, paths: tuple[str, ...], exclude: str = "") -> dict[str, str]:
    """Portable, content-based identity, independent of checkout path and timestamps."""
    files = {}

    def visit(path: Path) -> None:
        name = path.relative_to(root).as_posix()
        if name == exclude:
            return
        if _is_link(path):
            raise ValueError(f"不能校验符号链接：{path}")
        if path.is_dir():
            for child in path.iterdir():
                visit(child)
        elif path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()

    for name in paths:
        visit(root / name)
    return files


def source_hashes(project_root: Path) -> dict[str, str]:
    return file_hashes(project_root, WATCHED_PATHS)


def source_hash(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(f"{name}\0{files[name]}\n".encode("utf-8"))
    return digest.hexdigest()


def bundle_status(project_root: Path, dist: Path) -> tuple[bool, str]:
    """Verify build provenance and outputs; source-free distributions need no npm.

    This is a freshness/integrity check, not a signed software authenticity check.
    Existing legacy builds remain available through the explicit --no-build override.
    """
    if not (dist / "index.html").is_file():
        return False, "前端 dist/index.html 缺失"
    try:
        manifest = json.loads((dist / MANIFEST_NAME).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema") != 1:
            return False, "前端构建来源记录无效或版本不受支持"
        sources, outputs = manifest.get("sources"), manifest.get("outputs")
        for files in (sources, outputs):
            if not isinstance(files, dict) or not files or not all(
                isinstance(name, str) and isinstance(value, str)
                and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
                for name, value in files.items()
            ):
                return False, "前端构建来源记录不完整"
        if source_hash(sources) != manifest.get("sourceHash"):
            return False, "前端构建来源指纹无效"
        # Resolve only the bundle root, allowing a deliberately linked dist to be
        # verified against this checkout; nested links are not trusted build assets.
        if file_hashes(dist.resolve(), (".",), MANIFEST_NAME) != outputs:
            return False, "前端构建文件已变更或缺失"
        current_sources = source_hashes(project_root)
        if current_sources and current_sources != sources:
            return False, "前端源码、依赖锁文件或构建配置与现有 dist 不一致"
        mode = "已校验" if current_sources else "已校验预构建包（无本地源码）"
        version = manifest.get("version", "unknown")
        return True, f"前端 v{version} · 源码 {manifest['sourceHash'][:12]} · {mode}"
    except FileNotFoundError:
        return False, "前端缺少构建来源记录（旧版或未完成的构建）"
    except (OSError, ValueError, TypeError) as error:
        return False, f"无法校验前端构建：{error}"


def is_stale(project_root: Path, dist: Path) -> bool:
    return not bundle_status(project_root, dist)[0]


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
    try:
        linked = _is_link(project_root / "dist")
    except OSError as error:
        return False, f"无法检查前端构建目录：{error}"
    if linked:
        return False, (
            "dist 是符号链接或 Windows 目录联接，已停止构建以免覆盖其他目录。"
            "请手动改为本仓库的构建目录后重试，或用 --no-build 显式使用现有界面"
        )
    command = npm_command()
    if command is None:
        return False, "未找到 npm，无法构建前端。请安装 Node.js 与 npm 后重试"
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
        inputs = source_hashes(project_root)
        completed = subprocess.run(
            command,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=BUILD_TIMEOUT_SECONDS,
            # Vite loads configuration before buildStart. Bind its provenance to
            # the inputs that existed before npm/type-checking/config evaluation.
            env={**os.environ, EXPECTED_SOURCE_HASH_ENV: source_hash(inputs)},
        )
    except subprocess.TimeoutExpired as error:
        return False, (
            f"前端构建超时（{BUILD_TIMEOUT_SECONDS}s）：\n"
            f"{_build_output(error.output)}"
        )
    except (OSError, ValueError) as error:
        return False, f"前端构建无法启动：{error}"
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        return False, (
            f"前端构建失败（npm 退出码 {completed.returncode}）：\n"
            f"{_build_output(completed.stdout)}"
        )
    try:
        if source_hashes(project_root) != inputs:
            return False, "前端输入在构建期间发生变化，已停止启动；请重新运行 npm run build"
    except (OSError, ValueError) as error:
        return False, f"前端构建后无法校验输入：{error}"
    valid, provenance = bundle_status(project_root, project_root / "dist")
    if not valid:
        return False, f"构建命令成功，但前端校验失败：{provenance}\n请运行 npm run build 重试"
    return True, f"前端构建完成（{elapsed:.1f}s）\n{provenance}"
