"""Bounded architecture drill-down. Never traverse an import or execute source code."""

import ast
import hashlib
import html
import io
import posixpath
import re
import tokenize
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..domain.models import UmlDiagram
from ..infrastructure.repository import EXCLUDED, readable, root_path
from .class_extractors import SUPPORTED_SUFFIXES, parse_classes

MAX_COMPONENTS = 3
MAX_FILES = 12
MAX_CLASSES = 24
MAX_FILE_BYTES = 64_000
MAX_SOURCE_BYTES = 256_000
MAX_MEMBERS = 160
MAX_CLASS_MEMBERS = 32
MAX_BOUNDARIES = 24
MAX_UML_BYTES = 40_000
MAX_UML_LINES = 360
LIMITATIONS = [
    "只解析所选组件明确引用的文件；不会沿导入读取其他文件。",
    "仅展示静态类、字段、方法签名与可确定的继承；不推断调用、动态成员或运行时行为。",
    "边界节点只表示静态导入或直接架构邻居，不展开其内部实现。",
]


class ScopeError(ValueError):
    def __init__(self, status, message, limitations=None):
        super().__init__(message)
        self.status = status
        self.limitations = limitations or []


def fail(status, message, limitations=None):
    raise ScopeError(status, message, limitations)


@dataclass
class Selection:
    diagram: object
    nodes: list
    component_ids: list[str]
    files: list[str]
    architecture_revision: int
    narrowed: bool = False


def selection(project, component_ids, architecture_revision, file_paths=None):
    ids = sorted(set(component_ids))
    if not ids:
        fail("unmapped", "请先选择 1–3 个架构组件，再查看局部类细节。")
    if len(ids) > MAX_COMPONENTS:
        fail("too_large", "最多同时展开 3 个组件，请缩小选择范围。")
    if architecture_revision < 0:
        fail("unmapped", "架构版本不存在，请重新选择版本。")
    if architecture_revision:
        revision = next(
            (a for a in project.architectures if a.number == architecture_revision), None
        )
        diagram = revision.diagram if revision else None
    else:
        diagram = project.source_diagram
    if diagram is None:
        fail("unmapped", "架构版本不存在或尚未建立源码结构，请刷新基线或选择其他版本。")
    by_id = {n.id: n for n in diagram.nodes}
    if set(ids) - by_id.keys():
        fail("unmapped", "所选组件不属于这个架构版本，请重新选择。")
    nodes = [by_id[cid] for cid in ids]
    names = sorted({name for n in nodes for name in n.source_refs})
    if file_paths is not None:
        narrowed = sorted(set(file_paths))
        if not narrowed or set(narrowed) - set(names):
            fail("unmapped", "文件范围必须是所选组件明确引用的 1–12 个文件。")
        names = narrowed
    elif any(n.source_ref_count > len(n.source_refs) for n in nodes):
        fail("too_large", "组件的源码映射列表已截断，请从明确列出的文件中选择局部范围。")
    if len(names) > MAX_FILES:
        fail(
            "too_large", f"所选组件包含 {len(names)} 个文件，最多展开 12 个；请拆分组件或缩小选择。"
        )
    return Selection(diagram, nodes, ids, names, architecture_revision, file_paths is not None)


def selected_sources(project, scope):
    """Validate every bound first, then read only the explicit selected files."""
    unmapped = [n.label for n in scope.nodes if not n.source_refs]
    if unmapped:
        label = "DESIGN · " if scope.architecture_revision else ""
        fail("unmapped", label + "这些组件尚无明确源码映射：" + "、".join(unmapped))
    unsupported = [
        name
        for name in scope.files
        if PurePosixPath(name).suffix not in SUPPORTED_SUFFIXES | {".py"}
    ]
    if unsupported:
        fail(
            "unsupported",
            "当前局部类解析支持 Python、C++、TypeScript 与 Vue 脚本；未解析："
            + "、".join(unsupported),
        )
    if (
        not project.baseline
        or project.source_fingerprint != project.baseline.fingerprint
        or not project.source_file_fingerprints
    ):
        fail("unmapped", "源码索引缺少逐文件依据，请刷新基线后重试。")
    root = root_path(project.repository)
    paths, total = [], 0
    for name in scope.files:
        relative = PurePosixPath(name)
        path = root / name
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or any(part in EXCLUDED for part in relative.parts)
            or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents if parent != root)
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
            or not readable(path)
        ):
            fail("unmapped", "源码映射无效或文件不存在：" + name)
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_SOURCE_BYTES:
            fail("too_large", "所选源码超过单文件 64 KB 或合计 256 KB 的限制，请缩小范围。")
        if name not in project.source_file_fingerprints:
            fail("unmapped", "源码文件没有当前基线依据，请刷新基线：" + name)
        paths.append((name, path))
    texts, hashes = {}, {}
    for name, path in paths:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            fail("too_large", "源码文件已增大，请缩小范围：" + name)
        digest = hashlib.sha256(data).hexdigest()
        if digest != project.source_file_fingerprints[name]:
            fail("unmapped", "源码已经变化，请刷新基线后重试：" + name)
        try:
            encoding = "utf-8-sig"
            if name.endswith(".py"):
                encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            texts[name] = data.decode(encoding)
        except (SyntaxError, UnicodeError, LookupError):
            fail("unsupported", "无法读取源码编码：" + name)
        hashes[name] = digest
    return texts, hashes


def validate_class_source(source):
    """Apply the same render budget to hand-authored scoped designs and source views."""
    declarations = re.findall(
        r"(?m)^\s*(?:abstract\s+)?(?:class|enum|interface|entity|annotation|object|component|rectangle|database)\s+",
        source,
    )
    non_boundary = re.findall(
        r"(?m)^\s*(?:abstract\s+)?(?:class|enum|entity|annotation|object)\s+", source
    )
    if (
        len(source.encode()) > MAX_UML_BYTES
        or len(source.splitlines()) > MAX_UML_LINES
        or any(len(line) > 500 for line in source.splitlines())
        or len(non_boundary) > MAX_CLASSES
        or len(declarations) > MAX_CLASSES + MAX_BOUNDARIES
    ):
        fail("too_large", "局部类图超过实体、成员或文本上限，请缩小组件范围后重试。")
    total, count, in_entity = 0, 0, False
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("'"):
            continue
        declaration = re.match(
            r"(?:abstract\s+)?(?:class|enum|interface|entity|annotation|object)\s+", line
        )
        if declaration and "{" in line:
            _, body = line.split("{", 1)
            if body.strip() not in {"", "}"}:
                fail("too_large", "局部类图的每个成员必须单独一行，以便核验成员上限。")
            in_entity, count = body.strip() != "}", 0
        elif in_entity:
            if line == "}":
                in_entity = False
                continue
            punctuation = re.sub(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z]+);", "", line)
            if any(marker in punctuation for marker in (";", "{", "}")):
                fail("too_large", "局部类图的每个成员必须单独一行，以便核验成员上限。")
            count += 1
            total += 1
            if count > MAX_CLASS_MEMBERS or total > MAX_MEMBERS:
                fail("too_large", "局部类图超过每类 32 个或合计 160 个成员，请缩小范围。")


def safe(value):
    # Source identifiers, signatures and labels are data, never PlantUML syntax.
    return (
        html.escape(str(value), quote=True)
        .replace("\\", "&#92;")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
    )


def alias(prefix, name):
    return prefix + hashlib.sha256(name.encode()).hexdigest()[:14]


def expression(node):
    value = ast.unparse(node) if node is not None else ""
    if len(value) > 300:
        fail("too_large", "成员签名或字段定义过长，请缩小范围或使用接口级设计图。")
    return value


def classes_in(statements, prefix=""):
    for node in statements:
        if isinstance(node, ast.ClassDef):
            qualified = prefix + node.name
            yield qualified, node
            yield from classes_in(node.body, qualified + ".")
        elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Include statically declared conditional classes, but not function-local factories.
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(node, field, None)
                if isinstance(nested, list):
                    yield from classes_in(nested, prefix)
            for handler in getattr(node, "handlers", []):
                yield from classes_in(handler.body, prefix)


def arguments(args):
    """Keep signature structure/types, but do not copy potentially sensitive defaults."""

    def argument(item, default=False):
        result = item.arg + (": " + expression(item.annotation) if item.annotation else "")
        return result + (" = …" if default else "")

    positional = [*args.posonlyargs, *args.args]
    default_start = len(positional) - len(args.defaults)
    result = []
    for index, item in enumerate(positional):
        result.append(argument(item, index >= default_start))
        if args.posonlyargs and index + 1 == len(args.posonlyargs):
            result.append("/")
    if args.vararg:
        result.append("*" + argument(args.vararg))
    elif args.kwonlyargs:
        result.append("*")
    result.extend(
        argument(item, default is not None)
        for item, default in zip(args.kwonlyargs, args.kw_defaults)
    )
    if args.kwarg:
        result.append("**" + argument(args.kwarg))
    value = ", ".join(result)
    if len(value) > 300:
        fail("too_large", "方法签名过长，请缩小范围或使用接口级设计图。")
    return value


def parse_python(name, text):
    try:
        tree = ast.parse(text, filename=name)
    except (SyntaxError, ValueError, RecursionError):
        fail("unsupported", "Python 语法无法静态解析：" + name)
    classes = []
    for qualified, node in classes_in(tree.body):
        if len(classes) >= MAX_CLASSES:
            fail("too_large", "所选范围超过 24 个类，请拆分组件或缩小选择。")
        classes.append(
            {
                "name": qualified,
                "line": node.lineno,
                "members": members(node),
                "bases": [expression(base) for base in node.bases],
                "kind": "class",
            }
        )
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            if node.module:
                imports.append(prefix + node.module)
            else:
                imports.extend(prefix + item.name for item in node.names)
    return {"classes": classes, "imports": imports, "limitations": []}


def method_assignments(body):
    pending = list(reversed(body))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            yield node
        pending.extend(reversed(list(ast.iter_child_nodes(node))))


def members(node):
    fields, methods = {}, []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = arguments(item.args)
            result = expression(item.returns)
            label = ("async " if isinstance(item, ast.AsyncFunctionDef) else "") + item.name
            methods.append(
                ("- " if item.name.startswith("_") else "+ ")
                + label
                + "("
                + args
                + ")"
                + (" : " + result if result else "")
            )
            # Explicit instance attributes only; no inferred types or called-object traversal.
            candidates = method_assignments(item.body)
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            candidates = [item]
        else:
            continue
        for assignment in candidates:
            targets = (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and assignment is item:
                    name = target.id
                elif (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    name = target.attr
                else:
                    continue
                annotation = expression(getattr(assignment, "annotation", None))
                # Defaults may contain secrets; only show types, never literal source values.
                fields[name] = (
                    ("- " if name.startswith("_") else "+ ")
                    + name
                    + (" : " + annotation if annotation else "")
                )
    result = list(fields.values()) + methods
    if len(result) > MAX_CLASS_MEMBERS:
        fail("too_large", f"类 {node.name} 超过 32 个成员，请缩小范围或使用接口级设计图。")
    return result


def selected_import(source_name, target, file_names):
    """Match static module/path spelling against selected files, without opening a path.

    Ambiguous names, compiler aliases and external search paths remain boundaries.
    This identifies a package dependency, never a class invocation.
    """
    source_path = PurePosixPath(source_name)
    names = set(file_names)
    matches = set()
    if source_path.suffix == ".py":
        module = target
        if target.startswith("."):
            level = len(target) - len(target.lstrip("."))
            parent = source_path.parent.parts
            if level > len(parent) + 1:
                return None
            parent = parent[: len(parent) - level + 1]
            module = ".".join((*parent, target[level:])).strip(".")
        for name in names:
            path = PurePosixPath(name)
            if path.suffix != ".py":
                continue
            candidates = {path.with_suffix("").as_posix().replace("/", ".")}
            if path.name == "__init__.py":
                candidates.add(path.parent.as_posix().replace("/", "."))
            if module in candidates:
                matches.add(name)
    elif target.startswith("."):
        relative = posixpath.normpath((source_path.parent / target).as_posix())
        if relative == ".." or relative.startswith("../"):
            return None
        candidates = {relative}
        if not PurePosixPath(relative).suffix:
            for extension in SUPPORTED_SUFFIXES:
                candidates.add(relative + extension)
                candidates.add(relative + "/index" + extension)
        matches = names & candidates
    elif source_path.suffix in {
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".ipp",
        ".tpp",
    }:
        # An explicitly spelled header can match a selected header; no include-path search.
        matches = names & {target, posixpath.normpath((source_path.parent / target).as_posix())}
    return next(iter(matches)) if len(matches) == 1 else None


def extract(project, scope):
    texts, hashes = selected_sources(project, scope)
    classes, parsed, member_count, limitations = [], {}, 0, []
    for name, text in texts.items():
        try:
            result = parse_python(name, text) if name.endswith(".py") else parse_classes(name, text)
        except ScopeError:
            raise
        except (ValueError, RecursionError) as exc:
            fail("unsupported", "无法静态解析 " + name + "：" + str(exc))
        parsed[name] = result
        limitations.extend(result.get("limitations", []))
        for node in result["classes"]:
            if len(classes) >= MAX_CLASSES:
                fail("too_large", "所选范围超过 24 个类，请拆分组件或缩小选择。")
            rows = node["members"]
            member_count += len(rows)
            if len(rows) > MAX_CLASS_MEMBERS or any(len(row) > 400 for row in rows):
                fail("too_large", "类成员或签名超过局部细节上限，请缩小范围。")
            if member_count > MAX_MEMBERS:
                fail("too_large", "所选范围超过 160 个成员，请缩小组件范围。")
            qualified = node["name"]
            classes.append(
                (
                    name,
                    qualified,
                    node,
                    rows,
                    alias("C_", name + ":" + qualified + ":" + str(node["line"])),
                )
            )
    if not classes:
        fail(
            "empty",
            "所选文件未声明可展示的类或类型；模块级函数仍属于架构组件，不伪装成类。",
            limitations,
        )
    boundaries, edges = {}, set()
    selected_ids = set(scope.component_ids)
    component_alias = {n.id: alias("P_", n.id) for n in scope.nodes}
    nodes = {n.id: n for n in scope.diagram.nodes}

    def boundary(key, label):
        if key not in boundaries and len(boundaries) >= MAX_BOUNDARIES:
            fail("too_large", "所选范围超过 24 个边界依赖，请缩小组件范围。")
        boundaries[key] = label
        return alias("B_", key)

    for edge in scope.diagram.edges:
        mark = "DESIGN" if scope.architecture_revision else "SRC"
        if edge.source in selected_ids and edge.target in selected_ids:
            edges.add(
                (
                    component_alias[edge.source],
                    "..>",
                    component_alias[edge.target],
                    mark + " " + edge.label,
                )
            )
            continue
        inside, outside = None, None
        if edge.source in selected_ids and edge.target not in selected_ids:
            inside, outside = edge.source, edge.target
        elif edge.target in selected_ids and edge.source not in selected_ids:
            inside, outside = edge.target, edge.source
        if inside and outside in nodes:
            other = boundary("architecture:" + outside, mark + " · " + nodes[outside].label)
            left, right = (
                (component_alias[inside], other)
                if inside == edge.source
                else (other, component_alias[inside])
            )
            edges.add((left, "..>", right, mark + " " + edge.label))

    for name, result in parsed.items():
        owners = [n for n in scope.nodes if name in n.source_refs]
        for target in result["imports"]:
            target_file = selected_import(name, target, texts)
            if target_file is not None:
                target_owners = [n for n in scope.nodes if target_file in n.source_refs]
                for owner in owners:
                    for target_owner in target_owners:
                        if owner.id != target_owner.id:
                            edges.add(
                                (
                                    component_alias[owner.id],
                                    "..>",
                                    component_alias[target_owner.id],
                                    "SRC Import",
                                )
                            )
                continue
            other = boundary("import:" + target, "Import · " + target)
            for owner in owners:
                edges.add((component_alias[owner.id], "..>", other, "SRC Import"))
    # Conditional declarations can repeat a name; do not pick an arbitrary definition.
    occurrences = {}
    for name, qualified, _, _, cid in classes:
        occurrences.setdefault((name, qualified), []).append(cid)
    local_names = {key: ids[0] for key, ids in occurrences.items() if len(ids) == 1}
    for name, qualified, node, _, cid in classes:
        for identifier in node["bases"]:
            target = local_names.get((name, identifier))
            if target is None:
                target = boundary("base:" + identifier, "Base · " + identifier)
            edges.add((cid, "--|>", target, "SRC Extend"))

    lines = [
        "@startuml",
        "skinparam packageStyle rectangle",
        "skinparam linetype polyline",
        "hide empty members",
        "skinparam shadowing false",
        "left to right direction",
        f"' Commit: {project.baseline.commit}",
        "' Scope: " + ", ".join(scope.component_ids),
        f"' Architecture: {scope.architecture_revision}",
        "' Origin: source",
    ]
    emitted = set()
    for owner in scope.nodes:
        lines.append(f'package "{safe(owner.label)}" as {component_alias[owner.id]} {{')
        for name, qualified, node, rows, cid in classes:
            if name not in owner.source_refs or cid in emitted:
                continue
            emitted.add(cid)
            kind = safe(node.get("kind", "class"))
            lines.append(f'class "{safe(qualified)}" as {cid} <<SRC {kind}>> {{')
            lines.extend("  " + safe(row) for row in rows)
            lines.extend(["}", f"note bottom of {cid} : {safe(name)}:{node['line']}"])
        lines.append("}")
    for key, label in sorted(boundaries.items()):
        lines.append(f'interface "{safe(label)}" as {alias("B_", key)} <<Boundary>>')
    for left, relation, right, label in sorted(edges):
        lines.append(f"{left} {relation} {right} : {safe(label)}")
    lines.extend(
        [
            "legend bottom",
            "SRC · 所选文件当前基线的静态类声明",
            "Boundary · 仅保留边界，不读取或展开依赖内部",
            *(
                ["基线扫描不完整 · 本图仅核验所选文件，不证明整个仓库或验收"]
                if not project.baseline.complete
                else []
            ),
            *(
                ["DESIGN · 架构连线来自所选设计版本，不代表实现或验收"]
                if scope.architecture_revision
                else []
            ),
            "endlegend",
            "@enduml",
        ]
    )
    source = "\n".join(lines)
    validate_class_source(source)
    identity = (
        str(scope.architecture_revision)
        + ":"
        + ",".join(scope.component_ids)
        + ":"
        + ",".join(scope.files)
    )
    diagram = UmlDiagram(
        id=alias("classes_", identity),
        title="局部类细节 · " + " / ".join(n.label for n in scope.nodes)[:100],
        kind="class",
        source=source,
        scope="、".join(n.label for n in scope.nodes),
        origin="source",
        component_ids=scope.component_ids,
        architecture_revision=scope.architecture_revision,
        source_refs=scope.files,
        source_fingerprints=hashes,
        baseline_id=project.baseline.id,
    )
    return (
        diagram,
        [value for _, value in sorted(boundaries.items())],
        list(dict.fromkeys(limitations)),
    )
