import sys

from ..infrastructure.repository import files
from . import Candidate, adapter


@adapter("pytest")
def discover(root, milestone):
    tests = [
        p.relative_to(root).as_posix()
        for p in files(root)
        if p.name.startswith("test_") and p.suffix == ".py"
    ][:200]
    tokens = {
        part.lower()
        for scope in milestone.scope
        for part in scope.replace(".", "/").split("/")
        if len(part) > 2 and part not in {"src", "backend", "frontend", "tests"}
    }
    tests.sort(key=lambda p: (-sum(token in p.lower() for token in tokens), p))
    executable = next(
        (
            str(p)
            for p in (root / ".venv/Scripts/python.exe", root / ".venv/bin/python")
            if p.is_file()
        ),
        sys.executable,
    )
    return [
        Candidate(
            "pytest:" + path,
            "Pytest · " + path,
            [executable, "-m", "pytest", "-q", path, "--maxfail=1", "-p", "no:cacheprovider"],
            [path],
            "仅此测试文件；需要 Agent 说明与本里程碑的关联",
        )
        for path in tests[:12]
    ]
