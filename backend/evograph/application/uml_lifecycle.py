"""Class-model freshness depends on code baseline and design, never UI state."""

import hashlib
import json
import re

from ..domain.dependencies import ancestor_sets


def design_fingerprint(project):
    nodes = [*project.source_milestones, *project.milestones]
    ancestors = ancestor_sets({m.id: m.dependencies for m in nodes})
    data = {
        "architecture": project.architectures[-1].model_dump() if project.architectures else None,
        "milestones": [
            {
                **m.model_dump(
                    include={
                        "id",
                        "title",
                        "intent",
                        "scope",
                        "architecture_components",
                        "behavior_revision_ids",
                        "migration_steps",
                    }
                ),
                "prerequisites": sorted(ancestors[m.id]),
            }
            for m in project.milestones
        ],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def class_model_state(project):
    diagram = next((d for d in reversed(project.uml_diagrams) if d.id == "class_model"), None)
    reasons = []
    if diagram is None:
        reasons.append("尚未生成类图")
    else:
        if project.baseline and diagram.baseline_id != project.baseline.id:
            reasons.append("源码基线已更新")
        if diagram.design_fingerprint != design_fingerprint(project):
            reasons.append("设计已更新")
    return {"current": not reasons, "reasons": reasons}


def annotate_design(diagram):
    """Add text labels to the exported image too, not just the surrounding UI."""
    source = re.sub(
        r"(?ms)^' EVOGRAPH DESIGN BEGIN\n.*?^' EVOGRAPH DESIGN END\n?", "", diagram.source
    )
    if diagram.origin == "source" and diagram.design_elements:
        raise ValueError("源码图不能包含未实现设计，请使用 mixed")
    if diagram.origin == "mixed" and not diagram.design_elements:
        raise ValueError("混合类图必须用 design_elements 列出未实现的类或成员")
    notes = []
    for element in dict.fromkeys(diagram.design_elements):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?", element):
            raise ValueError("设计标识必须是 PlantUML 别名或 Alias::member")
        alias = element.split("::")[0]
        if not re.search(
            r"(?m)^\s*(?:abstract\s+)?(?:class|enum|interface|entity|annotation)\s+(?:\"[^\"\n]+\"\s+as\s+)?"
            + re.escape(alias)
            + r"(?=\s|\{|$)",
            source,
        ):
            raise ValueError("设计标识引用未声明的实体：" + alias)
        notes.append(f"note right of {element} : DESIGN · 设计待实现")
    if diagram.origin in {"mixed", "design"}:
        text = (
            "DESIGN · 全图为设计，尚未由源码证明"
            if diagram.origin == "design"
            else "SRC · 源码依据 | DESIGN · 标注部分为设计待实现"
        )
        notes.extend(["legend bottom", text, "endlegend"])
    if notes:
        block = "' EVOGRAPH DESIGN BEGIN\n" + "\n".join(notes) + "\n' EVOGRAPH DESIGN END\n"
        source = source.replace("@enduml", block + "@enduml")
    return source
