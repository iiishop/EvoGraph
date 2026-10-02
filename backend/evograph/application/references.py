"""Reference candidates used by the Agent composer @-mention picker."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.composer import ComposerDocument
from ..domain.models import Project
from ..infrastructure.repository import files, readable, root_path


@dataclass
class ResolvedComposer:
    content: str
    document: dict | None
    attachment_ids: list[str]
    references: list[dict]


class ReferenceService:
    """Expose project assets and safe repository paths without reading file contents."""

    def __init__(self, db):
        self.db = db

    def list(self, project_id: str):
        project = self.db.get(project_id)
        return {"items": self._materials(project), "truncated": False}

    def _materials(self, project: Project, include_repository: bool = True):
        items = [
            {
                "id": asset.id,
                "kind": "attachment",
                "name": asset.name,
                "label": asset.name,
                "detail": "项目资料",
                "path": "",
            }
            for asset in project.attachments
        ]
        if project.repository and include_repository:
            root = root_path(project.repository)
            for path in files(root):
                if not readable(path):
                    continue
                relative = path.relative_to(root).as_posix()
                items.append(
                    {
                        "id": f"repo:{relative}",
                        "kind": "repository",
                        "name": relative,
                        "label": relative,
                        "detail": "仓库文件",
                        "path": relative,
                    }
                )
        return items

    def _catalog(self, project: Project, include_repository: bool = True):
        items = self._materials(project, include_repository)
        for kind, milestones, detail in (
            ("milestone", project.milestones, "计划里程碑"),
            ("source_milestone", project.source_milestones, "SRC 源码能力"),
        ):
            items.extend(
                {
                    "id": milestone.id,
                    "kind": kind,
                    "name": milestone.title,
                    "label": milestone.title,
                    "detail": detail,
                    "path": " · ".join(milestone.scope[:3]),
                }
                for milestone in milestones
            )
        target = project.architectures[-1].diagram if project.architectures else None
        for kind, diagram, detail in (
            ("architecture_component", target, "目标架构组件"),
            ("source_component", project.source_diagram, "SRC 架构组件"),
        ):
            if diagram:
                items.extend(
                    {
                        "id": node.id,
                        "kind": kind,
                        "name": node.label,
                        "label": node.label,
                        "detail": detail,
                        "path": " · ".join(node.source_refs[:3]),
                    }
                    for node in diagram.nodes
                )
        return [{**item, "project_id": project.id} for item in items]

    def catalog(self, project_id: str):
        project = self.db.get(project_id)
        if project.archived:
            raise ValueError("项目已删除")
        warnings = []
        try:
            items = self._catalog(project)
        except (OSError, ValueError):
            # A disconnected repository must not hide independently saved graph
            # objects and materials. Repository admission still fails closed.
            items = self._catalog(project, include_repository=False)
            warnings.append("仓库文件暂不可用；仍可引用已保存资料与图中对象")
        return {"items": items, "truncated": False, "warnings": warnings}

    def resolve(
        self,
        project: Project,
        content: str,
        document: ComposerDocument | dict | None,
        attachment_ids: list[str],
    ) -> ResolvedComposer:
        """Resolve under the caller's operation lock, before consuming a question."""
        selected = list(dict.fromkeys(attachment_ids))
        canonical = None
        references = []
        if document is not None:
            canonical = ComposerDocument.model_validate(document).model_copy(deep=True)
            if canonical.plain_text() != content:
                raise ValueError("输入内容与行内引用不一致，请重新检查后发送")
            tokens = canonical.references()
            candidates = {
                (item["kind"], item["id"]): item
                for item in self._catalog(
                    project, include_repository=any(t.kind == "repository" for t in tokens)
                )
            }
            seen = set()
            for token in tokens:
                if token.project_id != project.id:
                    raise ValueError("引用来自其他项目，请移除或重新选择当前项目的对象")
                item = candidates.get((token.kind, token.id))
                if item is None:
                    raise ValueError(f"引用对象已不存在或不可用：{token.label}，请重新选择")
                token.label = item["label"]
                key = (token.kind, token.id)
                if key not in seen:
                    seen.add(key)
                    references.append(
                        {key: item[key] for key in ("project_id", "kind", "id", "label", "path")}
                    )
                if token.kind == "attachment" and token.id not in selected:
                    selected.append(token.id)
            # A current rename may be longer than the captured display label.
            canonical = ComposerDocument.model_validate(canonical.model_dump())
            content = canonical.plain_text()
        assets = {asset.id for asset in project.attachments}
        if len(selected) > 6 or set(selected) - assets:
            raise ValueError("每次最多引用 6 份本项目资料，请检查行内引用和已选资料")
        return ResolvedComposer(
            content, canonical.model_dump() if canonical else None, selected, references
        )
