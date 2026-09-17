"""Source-backed deliverable reconstruction, isolated from delivery/acceptance state."""

import hashlib

from pydantic import Field

from ..domain.dependencies import reduce_dependencies
from ..domain.models import Milestone, Model, ProposedMilestone, SourceBehavior
from ..infrastructure.repository import root_path, snapshot


class ReconstructedMilestone(ProposedMilestone):
    id: str = Field(pattern=r"^SRC_[A-Za-z0-9_-]{1,36}$")
    behaviors: list[SourceBehavior] = Field(min_length=1, max_length=12)


class BaselineMilestones(Model):
    baseline_id: str
    summary: str = Field(min_length=1, max_length=4000)
    milestones: list[ReconstructedMilestone]


class BaselineMilestoneService:
    def __init__(self, db):
        self.db = db

    def save(self, ctx, args: BaselineMilestones):
        p = self.db.get(ctx.project_id)
        if not p.baseline or args.baseline_id != p.baseline.id:
            raise ValueError("基线已变化，请读取当前基线后重新倒推")
        root = root_path(p.repository)
        ids = [m.id for m in args.milestones]
        if len(set(ids)) != len(ids) or set(ids) & {m.id for m in p.milestones}:
            raise ValueError("里程碑 ID 重复")
        known_components = {n.id for n in p.source_diagram.nodes} if p.source_diagram else set()
        if p.architectures:
            known_components.update(n.id for n in p.architectures[-1].diagram.nodes)
        old = {m.id: m for m in p.source_milestones}
        nodes = []
        for item in args.milestones:
            if len({b.key for b in item.behaviors}) != len(item.behaviors):
                raise ValueError("同一里程碑的行为 key 不能重复")
            if any(not item.dependency_reasons.get(dep, "").strip() for dep in item.dependencies):
                raise ValueError("每条前置依赖都需要具体理由")
            if set(item.architecture_components) - known_components:
                raise ValueError("架构组件不存在")
            if set(item.attachment_ids) - {a.id for a in p.attachments}:
                raise ValueError("附件不存在")
            refs = sorted({ref for b in item.behaviors for ref in b.source_refs})
            for ref in refs:
                path = (root / ref).resolve()
                if not path.is_relative_to(root) or ref not in ctx.receipts:
                    raise ValueError("行为依据只能引用本轮实际读取的源码文件")
                if hashlib.sha256(path.read_bytes()).hexdigest() != ctx.receipts[ref]:
                    raise ValueError("源码发生变化，请刷新基线后重新读取")
            nodes.append(
                Milestone(
                    **item.model_dump(exclude={"behaviors"}),
                    origin="source",
                    status="IMPLEMENTED",
                    source_refs=refs,
                    source_behaviors=item.behaviors,
                    source_baseline_id=p.baseline.id,
                    position=old[item.id].position if item.id in old else None,
                )
            )
        # Validate the entire graph and reduce it before replacing the previous
        # snapshot. A malformed model response must never partially erase it.
        reduce_dependencies(nodes)
        current = snapshot(p.repository, 0)
        if (
            not current.complete
            or not p.baseline.complete
            or current.fingerprint != p.baseline.fingerprint
        ):
            raise ValueError("源码基线已变化或不完整，请刷新基线后重新倒推")
        if (
            nodes == p.source_milestones
            and p.source_analysis_baseline_id == p.baseline.id
            and args.summary == p.source_analysis_summary
        ):
            return {"status": "NO_PROGRESS"}
        p.source_milestones = nodes
        p.source_analysis_baseline_id = p.baseline.id
        p.source_analysis_summary = args.summary
        self.db.save(p, "baseline_milestones_reconstructed", args.summary)
        return {
            "node_ids": [m.id for m in nodes],
            "effect": "updated",
            "message": f"已从源码倒推 {len(nodes)} 个已实现里程碑",
        }
