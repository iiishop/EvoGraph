"""Deterministic source observations, kept separate from planned PRs and acceptance.

No source is executed. Directory groups are evidence of code presence, not claims
about runtime behavior. Import edges are observed relations, never prerequisites.
"""

import ast
import hashlib
import re
from collections import defaultdict

from ..domain.models import Diagram, DiagramEdge, DiagramNode
from ..infrastructure.repository import files, readable, root_path

EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".sql", ".go", ".rs", ".java"}


def imports(path, text):
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        result = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                result.extend(n.name.replace(".", "/") for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                prefix = "/".join(path.parts[: -node.level]) + "/" if node.level else ""
                result.append(prefix + node.module.replace(".", "/"))
        return result
    return re.findall(r"(?:from\s+|import\s*|require\s*\() [\'\"]([^\'\"]+)", text, re.X)


def role_for(names):
    suffixes = {p.suffix for p in names}
    if ".sql" in suffixes:
        return "database"
    if suffixes & {".vue", ".tsx", ".jsx"}:
        return "frontend"
    return "backend"


def populate(project):
    root = root_path(project.repository)
    groups, records = defaultdict(list), []
    truncated = False
    for index, path in enumerate(files(root)):
        if index >= 10000 or len(records) >= 1200:
            truncated = True
            break
        if path.suffix not in EXTENSIONS or not readable(path) or path.stat().st_size > 100000:
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        group = "/".join(parts[: min(3, len(parts) - 1)]) or "根目录"
        groups[group].append(relative)
        records.append((relative, group, path.read_text(encoding="utf-8", errors="replace")))
    selected = sorted(groups, key=lambda g: (-len(groups[g]), g))[:30]
    ids = {g: "SRC_" + hashlib.sha256(g.encode()).hexdigest()[:10] for g in selected}
    nodes = []
    for group in selected:
        paths = [p.as_posix() for p in groups[group]]
        description = f"从 {len(paths)} 个源码文件观察到的目录组件；不代表功能已验收。"
        nodes.append(
            DiagramNode(
                id=ids[group],
                label=group,
                description=description,
                role=role_for(groups[group]),
                source_refs=paths[:40],
            )
        )
    lookup = {p.with_suffix("").as_posix(): group for p, group, _ in records}
    relations = set()
    for path, group, text in records:
        if group not in ids:
            continue
        for item in imports(path, text):
            if item.startswith("."):
                resolved = (root / path.parent / item).resolve()
                if not resolved.is_relative_to(root):
                    continue
                item = resolved.relative_to(root).as_posix()
            item = re.sub(r"\.(?:tsx?|jsx?|vue|py)$", "", item)
            target = (
                lookup.get(item) or lookup.get(item + "/index") or lookup.get(item + "/__init__")
            )
            if target in ids and target != group:
                relations.add((ids[group], ids[target]))
    project.source_diagram = (
        Diagram(
            id="source_architecture",
            title="源码结构",
            nodes=nodes,
            edges=[
                DiagramEdge(source=a, target=b, label="导入") for a, b in sorted(relations)[:100]
            ],
        )
        if nodes
        else None
    )
    project.source_fingerprint = project.baseline.fingerprint
    project.source_summary = (
        f"扫描 {len(records)} 个源码文件，显示 {len(nodes)} 个目录组件、"
        f"{min(len(relations), 100)} 条可解析的静态导入关系。动态调用与别名解析不在此视图范围。"
        + (
            " 已达到扫描或展示上限。"
            if truncated or len(groups) > 30 or len(relations) > 100
            else ""
        )
    )
