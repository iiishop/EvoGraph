"""Versioned PlantUML documents and an isolated, local renderer."""

import base64
import os
import re
import shutil
import subprocess
from pathlib import Path
from functools import lru_cache

from ..domain.models import UmlDiagram
from ..infrastructure.repository import readable, root_path

STYLE = """Use PlantUML for UML. Maintain one canonical class diagram (id=class_model),
an evidence-driven code architecture overview, not merely OO inheritance. Follow:
outer scope package, responsibility-based rectangular subpackages, no invented classes;
Python file functions <<Module>>, dict shapes <<Data Contract>>, records <<Data Record>>,
actual dataclass/enum/Exception, tables <<SQLite Table>> with PK/FK/UNIQUE, dependencies
<<External>>, registries <<Registry>> and tools <<ToolDef>>. Include critical fields,
types/defaults and method signatures (+ public, - private). Prefer member endpoints.
Label relations Invoke, Use, Create, Register, Contain, Persist, Handoff, Dispatch,
Inject, Extend (real inheritance only), Discover, Sync, Request, Throw, Foreign Key.
Use *-- for structural containment, directed dependencies, --|> for real inheritance.
Add Chinese notes near specific members explaining numbered flows, failure paths,
constraints and risks; preserve original code identifiers. Keep the main data/control
chain complete. Use skinparam packageStyle rectangle, skinparam linetype polyline,
hide empty members. Group the main chain with together. Do not rely on colors.
Record commit and scope. Read source first; origin=source requires actual source_refs.
Do not mix unimplemented design into source observations. Prefer a single maintained
overview; sequence/activity/state diagrams may be linked to milestone_ids as needed.
Do not use includes, macros, hyperlinks, embedded images, or remote resources.
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
        raise ValueError(
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
        raise ValueError("UML 编译超时，请简化布局约束后重试") from exc
    if result.returncode or b"<svg" not in result.stdout:
        raise ValueError(
            "PlantUML 编译失败，请修正语法："
            + result.stderr.decode("utf-8", errors="replace")[-1000:]
        )
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
        if diagram.kind == "class":
            diagram.id = "class_model"
            for required in (
                "skinparam packageStyle rectangle",
                "skinparam linetype polyline",
                "hide empty members",
            ):
                if required not in diagram.source:
                    raise ValueError("类图必须遵守绘图规范：" + required)
        if diagram.origin == "source":
            if not p.baseline or not diagram.source_refs:
                raise ValueError("源码类图需要基线及真实源码引用")
            root = root_path(p.repository)
            for name in diagram.source_refs:
                path = (root / name).resolve()
                if not path.is_relative_to(root) or not path.is_file() or not readable(path):
                    raise ValueError("无效源码引用：" + name)
            diagram.baseline_id = p.baseline.id
        else:
            diagram.baseline_id = ""
        # The application supplies authoritative scope/version metadata, not the model.
        diagram.source = re.sub(r"(?im)^' (Commit|Scope|Origin):.*\n?", "", diagram.source)
        first, _, body = diagram.source.partition("\n")
        commit = p.baseline.commit if p.baseline else "unbound"
        diagram.source = first + f"\n' Commit: {commit}\n' Scope: {diagram.scope.replace(chr(10), ' ')}\n' Origin: {diagram.origin}\n" + body
        previous = [d for d in p.uml_diagrams if d.id == diagram.id]
        if previous and previous[-1].kind != diagram.kind:
            raise ValueError("同一图的类型不能变更，请使用新 ID")
        if previous and previous[-1].model_dump(exclude={"revision"}) == diagram.model_dump(exclude={"revision"}):
            return {"node_ids": [], "effect": "updated", "status": "NO_PROGRESS"}
        render(diagram.source)  # Store only diagrams that actually compile.
        diagram.revision = len(previous) + 1
        p.uml_diagrams.append(diagram)
        self.db.save(p, "uml_updated", f"{diagram.title} v{diagram.revision}")
        return {"node_ids": diagram.milestone_ids, "effect": "updated"}

    def preview(self, project_id: str, diagram_id: str, revision: int = 0):
        p = self.db.get(project_id)
        matches = [
            d
            for d in p.uml_diagrams
            if d.id == diagram_id and (not revision or d.revision == revision)
        ]
        if not matches:
            raise ValueError("UML 图不存在")
        return {
            "image": "data:image/svg+xml;base64,"
            + base64.b64encode(render(matches[-1].source)).decode()
        }
