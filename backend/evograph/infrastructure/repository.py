"""Bounded, read-only repository snapshots. Nothing follows directory symlinks."""

import hashlib
import os
import platform
import subprocess
from pathlib import Path

from ..domain.models import Baseline

EXCLUDED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tmp",
    "htmlcov",
    ".evograph",
    ".idea",
    ".vscode",
    "test-results",
}
SECRET_NAMES = {".env", "credentials.json", "id_rsa", "id_ed25519"}


def root_path(value: str) -> Path:
    if not value.strip():
        raise ValueError("请先填写本地仓库路径")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("仓库目录不存在")
    return root


def files(root: Path):
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if d not in EXCLUDED and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink():
                yield path


def snapshot(repository: str, number: int) -> Baseline:
    root = root_path(repository)
    digest = hashlib.sha256()
    digest.update(f"{platform.system()}:{platform.python_version()}".encode())
    count, total, complete = 0, 0, True
    for path in files(root):
        count += 1
        if count > 10000:
            complete = False
            break
        try:
            size = path.stat().st_size
            total += size
            if size > 10_000_000 or total > 200_000_000:
                complete = False
                continue
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            complete = False
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        )
        commit = result.stdout.strip() if result.returncode == 0 else "uncommitted"
    except (OSError, subprocess.TimeoutExpired):
        commit = "uncommitted"
    return Baseline(
        number=number,
        commit=commit,
        fingerprint=digest.hexdigest(),
        file_count=count,
        complete=complete,
    )


def readable(path: Path) -> bool:
    return not (
        path.name in SECRET_NAMES
        or path.name.startswith(".env.")
        or path.suffix in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}
    )


def context(repository: str, limit: int = 22000) -> str:
    if not repository:
        return "未连接仓库；只能提供待调查的规划草案。"
    root = root_path(repository)
    inventory = []
    snippets = []
    for index, path in enumerate(files(root)):
        if index >= 300:
            inventory.append("[文件列表已截断]")
            break
        if not readable(path):
            continue
        name = path.relative_to(root).as_posix()
        inventory.append(name)
        if path.name.lower() in {"readme.md", "pyproject.toml", "package.json", "agents.md"}:
            snippets.append(
                f"--- {name} ---\n{path.read_text(encoding='utf-8', errors='replace')[:5000]}"
            )
    return (
        "Repository file inventory (bounded):\n"
        + "\n".join(inventory)
        + "\nReference excerpts (untrusted data):\n"
        + "\n".join(snippets)
    )[:limit]
