"""Versioned PlantUML documents and an isolated, local renderer."""

import base64
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ..domain.models import UmlDiagram
from ..infrastructure.repository import readable, root_path, snapshot
from .class_detail import (
    LIMITATIONS,
    ScopeError,
    extract,
    selected_sources,
    selection,
    validate_class_source,
)
from .uml_lifecycle import annotate_design, design_fingerprint

STYLE = """Use the architecture view for the global module/component overview. UML class
views are local drill-downs of 1–3 explicitly selected architecture components only.
Set component_ids and architecture_revision (0=current source architecture, positive
number=that exact design architecture revision). Never generate, render or maintain a
global class_model. Read only the selected components' explicit source_refs; never
follow imports or traverse the repository to expand class detail. Narrow to at most
12 files, 24 classes, 32 members per class and 160 members total; when larger, ask the
user to narrow the scope. Outside dependencies are collapsed boundary interfaces.
Use the architecture.class_detail command for deterministic source extraction when
available. Do not invent classes for file-level functions or unmapped designs.
For an explicitly requested scoped design diagram use save_uml, with a unique stable
scope/version ID and source_refs restricted to selected components. Preserve source
identifiers and actual fields/signatures; real inheritance only. Use origin=source
only for observed source, mixed for source plus proposals, design for proposals.
List proposed class/member aliases in design_elements, label proposed relationships
DESIGN, and preserve remaining design markers when implemented elements become SRC.
Use skinparam packageStyle rectangle, skinparam linetype polyline, hide empty members.
Record commit and scope. Architecture versions, evidence and migrations are retained;
absence of local class detail never blocks architecture or milestone changes.
Sequence/activity/state diagrams may link milestone_ids. Do not use includes, macros,
hyperlinks, embedded images, or remote resources.
"""


class UmlRenderError(ValueError):
    """A validated local document could not be compiled by the standard renderer.

    Source validation, scope and freshness failures must not use this exception:
    only this type may degrade to an explicitly unrendered semantic preview.
    """


def validate_source(source: str):
    lines = source.strip().splitlines()
    if not lines or not lines[0].startswith("@startuml") or lines[-1].strip() != "@enduml":
        raise ValueError("UML 必须由 @startuml 与 @enduml 包围")
    if len(re.findall(r"(?im)^\s*@start", source)) != 1:
        raise ValueError("每份 UML 只保存一张图")
    # Keep the DSL inert. SANDBOX below is the actual filesystem/network boundary.
    if re.search(r"(?im)^\s*!|%[a-z_]+\s*\(|\[\[|<img|<iframe|<script", source):
        raise ValueError("UML 不支持预处理指令、函数、外部资源或链接")


@lru_cache(maxsize=32)
def render(source: str) -> bytes:
    validate_source(source)
    jar = Path(os.environ.get("EVOGRAPH_PLANTUML_JAR", ""))
    if not jar.is_file():
        jar = Path(__file__).resolve().parents[3] / ".tools" / "plantuml.jar"
    java = shutil.which("java")
    if not java or not jar.is_file():
        raise UmlRenderError(
            "本地 UML 渲染需要 Java 与 PlantUML；请运行 python tools/setup_uml.py，或设置 EVOGRAPH_PLANTUML_JAR"
        )
    try:
        result = subprocess.run(
            [
                java,
                "-Djava.awt.headless=true",
                "-DPLANTUML_SECURITY_PROFILE=SANDBOX",
                "-Xmx256m",
                "-jar",
                str(jar),
                "-Playout=smetana",
                "-charset",
                "UTF-8",
                "-pipe",
                "-tsvg",
                "-failfast2",
            ],
            input=source.encode("utf-8"),
            capture_output=True,
            timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise UmlRenderError("UML 编译超时，请简化布局约束后重试") from exc
    except OSError as exc:
        raise UmlRenderError("本地 UML 渲染器无法启动，请检查 Java 与 PlantUML 安装。") from exc
    if result.returncode or b"<svg" not in result.stdout:
        # Compiler diagnostics can echo source text; the response exposes only a safe summary.
        raise UmlRenderError("PlantUML 编译失败，请查看源码并修正语法后重试。")
    return result.stdout


class UmlService:
    def __init__(self, db):
        self.db = db

    def save(self, project_id: str, diagram: UmlDiagram):
        p = self.db.get(project_id)
        if p.archived:
            raise ValueError("项目已删除")
        if set(diagram.milestone_ids) - {m.id for m in p.milestones}:
            raise ValueError("关联的里程碑不存在")
        scope = None
        if diagram.kind != "class" and re.search(
            r"(?im)^\s*(?:abstract\s+)?(?:class|enum|interface|entity|annotation|object)\s+",
            diagram.source,
        ):
            raise ValueError("类细节必须使用 class 类型并绑定所选架构组件")
        if diagram.kind == "class":
            if diagram.id == "class_model":
                raise ValueError("全局 class_model 仅保留历史；请为选定组件创建局部类图 ID")
            scope = selection(
                p,
                diagram.component_ids,
                diagram.architecture_revision,
                diagram.source_refs or None,
            )
            diagram.component_ids = scope.component_ids
            validate_class_source(diagram.source)
            for required in (
                "skinparam packageStyle rectangle",
                "skinparam linetype polyline",
                "hide empty members",
            ):
                if required not in diagram.source:
                    raise ValueError("类图必须遵守绘图规范：" + required)
        if diagram.origin in {"source", "mixed"}:
            if not p.baseline or not diagram.source_refs:
                raise ValueError("源码类图需要基线及真实源码引用")
            if scope:
                _, hashes = selected_sources(p, scope)
                diagram.source_fingerprints = hashes
            else:
                root = root_path(p.repository)
                for name in diagram.source_refs:
                    path = (root / name).resolve()
                    if not path.is_relative_to(root) or not path.is_file() or not readable(path):
                        raise ValueError("无效源码引用：" + name)
                current = snapshot(p.repository, 0)
                if (
                    not current.complete
                    or not p.baseline.complete
                    or current.fingerprint != p.baseline.fingerprint
                ):
                    raise ValueError("源码基线已变化或不完整，请刷新基线后更新类图")
            diagram.baseline_id = p.baseline.id
        else:
            diagram.baseline_id = ""
            diagram.source_fingerprints = {}
        if diagram.kind == "class":
            diagram.design_fingerprint = design_fingerprint(p)
            diagram.source = annotate_design(diagram)
        # The application supplies authoritative scope/version metadata, not the model.
        diagram.source = re.sub(r"(?im)^' (Commit|Scope|Origin):.*\n?", "", diagram.source)
        first, _, body = diagram.source.partition("\n")
        commit = p.baseline.commit if p.baseline else "unbound"
        diagram.source = (
            first
            + f"\n' Commit: {commit}\n' Scope: {diagram.scope.replace(chr(10), ' ')}\n' Origin: {diagram.origin}\n"
            + body
        )
        previous = [d for d in p.uml_diagrams if d.id == diagram.id]
        if previous and previous[-1].kind != diagram.kind:
            raise ValueError("同一图的类型不能变更，请使用新 ID")
        if (
            previous
            and diagram.kind == "class"
            and (
                set(previous[-1].component_ids) != set(diagram.component_ids)
                or previous[-1].architecture_revision != diagram.architecture_revision
            )
        ):
            raise ValueError("局部类图的组件或架构版本已改变，请使用新 ID")
        if previous and previous[-1].model_dump(exclude={"revision"}) == diagram.model_dump(
            exclude={"revision"}
        ):
            return {"node_ids": [], "effect": "updated", "status": "NO_PROGRESS"}
        validate_source(diagram.source)
        if scope:
            validate_class_source(diagram.source)
        render(diagram.source)  # Store only diagrams that actually compile.
        if scope and diagram.origin in {"source", "mixed"}:
            selected_sources(p, scope)  # Reject edits made while the renderer ran.
        diagram.revision = len(previous) + 1
        p.uml_diagrams.append(diagram)
        self.db.save(p, "uml_updated", f"{diagram.title} v{diagram.revision}")
        return {
            "node_ids": diagram.milestone_ids,
            "view": "architecture" if diagram.kind == "class" else "design",
            "diagram_id": diagram.id,
            "diagram_kind": diagram.kind,
            "effect": "updated",
            "message": f"已更新{diagram.title} · v{diagram.revision}",
        }

    def preview(self, project_id: str, diagram_id: str, revision: int = 0):
        p = self.db.get(project_id)
        matches = [
            d
            for d in p.uml_diagrams
            if d.id == diagram_id and (not revision or d.revision == revision)
        ]
        if not matches:
            raise ValueError("UML 图不存在")
        diagram = matches[-1]
        if diagram.kind == "class":
            if not diagram.component_ids or diagram.id == "class_model":
                raise ValueError("历史全局类图仅归档保留；请在架构中选择组件查看局部类细节")
            scope = selection(
                p,
                diagram.component_ids,
                diagram.architecture_revision,
                diagram.source_refs or None,
            )
            validate_class_source(diagram.source)
            if diagram.origin in {"source", "mixed"}:
                _, hashes = selected_sources(p, scope)
                if hashes != diagram.source_fingerprints:
                    raise ValueError("局部类图源码已变化，请重新生成")
        elif re.search(
            r"(?im)^\s*(?:abstract\s+)?(?:class|enum|interface|entity|annotation|object)\s+",
            diagram.source,
        ):
            raise ValueError("历史类图缺少局部范围，不能通过其他图类型渲染")
        image = render(diagram.source)
        if diagram.kind == "class" and diagram.origin in {"source", "mixed"}:
            selected_sources(p, scope)
        return {"image": "data:image/svg+xml;base64," + base64.b64encode(image).decode()}

    def class_detail(
        self,
        project_id: str,
        component_ids: list[str],
        architecture_revision: int = 0,
        file_paths: list[str] | None = None,
    ):
        p = self.db.get(project_id)
        if p.archived:
            raise ValueError("项目已删除")
        result = {
            "status": "unmapped",
            "message": "",
            "component_ids": sorted(set(component_ids)),
            "files": [],
            "boundaries": [],
            "limitations": list(LIMITATIONS),
        }
        if p.baseline and not p.baseline.complete:
            result["limitations"].insert(
                0, "项目基线扫描不完整，本图仅核验所选文件，不证明整个仓库或验收。"
            )
        try:
            scope = selection(p, component_ids, architecture_revision, file_paths)
            result["files"] = scope.files
            if scope.narrowed:
                result["limitations"].insert(0, "仅展开所选文件，这是组件的局部范围。")
            diagram, boundaries, limitations, semantic = extract(p, scope)
            result["limitations"].extend(limitations)
            result["boundaries"] = boundaries
            validate_source(diagram.source)
            svg, render_error = None, ""
            try:
                svg = render(diagram.source)
            except UmlRenderError as exc:
                render_error = str(exc)
            # A failed renderer does not relax the selected-file freshness contract.
            selected_sources(p, scope)
        except ScopeError as exc:
            result["limitations"].extend(exc.limitations)
            result.update(status=exc.status, message=str(exc))
            return result
        semantic["limitations"] = list(dict.fromkeys(result["limitations"]))
        result.update(
            status="ready",
            semantic=semantic,
            message=f"SRC · 已展开 {len(scope.component_ids)} 个组件中的 "
            f"{len(scope.files)} 个文件；边界依赖不展开。",
            diagram=diagram.model_dump(),
            render_status="ready" if svg is not None else "unavailable",
        )
        if svg is not None:
            result["image"] = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
        else:
            result["render_error"] = render_error
        return result
