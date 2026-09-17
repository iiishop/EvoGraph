import json
import shutil

from ..infrastructure.repository import files
from . import Candidate, adapter


@adapter("node-scripts")
def discover(root, milestone):
    found = []
    for manifest in files(root):
        if manifest.name != "package.json" or manifest.stat().st_size > 100000:
            continue
        try:
            scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {})
        except (ValueError, OSError):
            continue
        directory = manifest.parent.relative_to(root).as_posix()
        for name in ("test:unit", "test:smoke", "typecheck", "lint", "test"):
            script = scripts.get(name, "")
            if not script or any(
                flag in script for flag in ("--watch", "--ui", "vitest --", "jest --watch")
            ):
                continue
            if "vitest" in script and "run" not in script:
                continue
            found.append(
                Candidate(
                    f"npm:{directory}:{name}",
                    f"{directory} · {name}",
                    [shutil.which("npm") or "npm", "--prefix", str(manifest.parent), "run", name],
                    [manifest.relative_to(root).as_posix()],
                    "执行仓库声明的脚本（包含生命周期脚本），最多 60 秒；静态检查不代表功能验收",
                )
            )
        if len(found) >= 12:
            break
    return found
