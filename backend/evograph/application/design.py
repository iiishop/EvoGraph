"""Versioned architecture decisions and reusable, validated visual descriptions."""

from ..domain.models import ArchitectureRevision, ArchitectureSpec, Diagram, Obligation
from ..infrastructure.repository import EXCLUDED, readable, root_path


def validate_diagram(diagram: Diagram):
    ids = {n.id for n in diagram.nodes}
    if len(ids) != len(diagram.nodes):
        raise ValueError("图中节点 ID 重复")
    seen = set()
    for edge in diagram.edges:
        if edge.source not in ids or edge.target not in ids:
            raise ValueError("图中连线引用不存在的节点")
        key = (edge.source, edge.target, edge.label)
        if key in seen:
            raise ValueError("图中存在重复连线")
        seen.add(key)


class DesignService:
    def __init__(self, db):
        self.db = db

    def save_diagram(self, project_id: str, diagram: Diagram):
        validate_diagram(diagram)
        p = self.db.get(project_id)
        if p.archived:
            raise ValueError("项目已删除")
        if set(diagram.milestone_ids) - {m.id for m in p.milestones}:
            raise ValueError("关联的里程碑不存在")
        if set(diagram.attachment_ids) - {a.id for a in p.attachments}:
            raise ValueError("关联的资料不存在")
        p.diagrams = [d for d in p.diagrams if d.id != diagram.id] + [diagram]
        self.db.save(p, "diagram_updated", diagram.title)
        return {"node_ids": diagram.milestone_ids, "effect": "updated"}

    def update(self, project_id: str, specification: ArchitectureSpec):
        validate_diagram(specification.diagram)
        p = self.db.get(project_id)
        if p.archived:
            raise ValueError("项目已删除")
        component_ids = {n.id for n in specification.diagram.nodes}
        refs = {name for node in specification.diagram.nodes for name in node.source_refs}
        if refs:
            root = root_path(p.repository)
            for name in refs:
                lexical = root / name
                path = lexical.resolve()
                if (
                    not path.is_relative_to(root)
                    or not path.is_file()
                    or not readable(path)
                    or any(part in EXCLUDED for part in path.relative_to(root).parts)
                    or lexical.is_symlink()
                    or any(parent.is_symlink() for parent in lexical.parents)
                ):
                    raise ValueError("架构引用的源码不存在或不可读取：" + name)
        if set(specification.research_ids) - {r.id for r in p.research}:
            raise ValueError("引用的网页依据不存在，请先搜索或读取网页")
        if specification.diagram.kind != "architecture":
            raise ValueError("架构设计必须使用 architecture 图类型")
        old_ids = {n.id for n in p.architectures[-1].diagram.nodes} if p.architectures else set()
        removed = old_ids - component_ids
        retired = {step.component_id for step in specification.retirements}
        if removed != retired:
            raise ValueError("删除的旧组件必须在 retirements 中逐项安排到迁移里程碑；当前图只保留最新组件")
        for step in specification.retirements:
            owner = p.milestone(step.milestone_id)
            if owner.status == "VERIFIED_COMPLETE":
                raise ValueError("迁移步骤必须安排到尚未完成的里程碑")
        if any(set(m.architecture_components) - component_ids - removed for m in p.milestones):
            raise ValueError("里程碑引用未知架构组件")
        if any(m.lease_active for m in p.milestones):
            raise ValueError("有里程碑正在执行，请先释放再修改架构")
        if (
            p.architectures
            and p.architectures[-1].model_dump(exclude={"number", "created_at"})
            == specification.model_dump()
        ):
            return {"node_ids": [], "effect": "updated", "status": "NO_PROGRESS"}
        version = ArchitectureRevision(
            **specification.model_dump(), number=len(p.architectures) + 1
        )
        p.architectures.append(version)
        for step in specification.retirements:
            step = step.model_copy(update={"from_revision": version.number - 1})
            p.milestone(step.milestone_id).migration_steps.append(step)
        for m in p.milestones:
            m.architecture_components = [cid for cid in m.architecture_components if cid in component_ids]
            # Architecture and stack constraints can affect even previously unmapped work.
            m.architecture_revision = version.number
            m.obligations = [o for o in m.obligations if o.id != "architecture"] + [
                Obligation(
                    id="architecture", label=f"审查架构 A{version.number} 与当前里程碑的一致性"
                )
            ]
            if m.status != "PLANNED":
                m.status = "REVALIDATION_REQUIRED"
        self.db.save(p, "architecture_updated", f"A{version.number} · {version.summary}")
        return {"node_ids": [m.id for m in p.milestones], "effect": "updated"}
