"""Incremental graph edits, checked and persisted before broadcasting to the UI."""

from ..domain.models import (
    BehaviorRevision,
    Milestone,
    Obligation,
    PlanningRevision,
    PlanProposal,
    ProposedMilestone,
    TargetVersion,
)
from ..domain.policies import obligations, validate_plan


class GraphEditor:
    def __init__(self, db):
        self.db = db

    def _check(self, project):
        if project.archived:
            raise ValueError("项目已删除")
        if not project.milestones:
            return
        by_id = {b.id: b for b in project.behaviors}
        proposed = []
        for node in project.milestones:
            behaviors = [by_id[bid] for bid in node.behavior_revision_ids]
            if any(b.owner != node.id for b in behaviors):
                raise ValueError("行为验收归属不一致")
            proposed.append(
                ProposedMilestone(
                    **node.model_dump(
                        include={
                            "id",
                            "title",
                            "intent",
                            "scope",
                            "architecture_components",
                            "dependencies",
                            "dependency_reasons",
                            "resources",
                            "change_types",
                        }
                    ),
                    behaviors=[
                        {"key": b.behavior_key, "statement": b.statement} for b in behaviors
                    ],
                )
            )
        validate_plan(
            PlanProposal(
                target="Incremental working graph", summary="Tool change", milestones=proposed
            )
        )

    def _save(self, project, summary, before):
        self._check(project)
        old_topology = [(m["id"], m["dependencies"]) for m in before]
        if old_topology != [(m.id, m.dependencies) for m in project.milestones]:
            for node in project.milestones:
                node.position = None
        project.proposal = None
        project.proposal_revision = None
        project.plans.append(
            PlanningRevision(
                number=len(project.plans) + 1,
                target_version=project.targets[-1].number if project.targets else 0,
                summary=summary,
                milestone_ids=[m.id for m in project.milestones],
            )
        )
        import json

        return self.db.save(
            project,
            "graph_edited",
            json.dumps(
                {
                    "summary": summary,
                    "before": before,
                    "after": [m.model_dump() for m in project.milestones],
                },
                ensure_ascii=False,
            ),
        )

    def upsert(self, project_id: str, proposed: ProposedMilestone, create: bool):
        if proposed.id.startswith("SRC_"):
            raise ValueError("SRC_ 是源码观察节点的保留命名空间，请为交付节点使用独立 ID")
        p = self.db.get(project_id)
        old = next((m for m in p.milestones if m.id == proposed.id), None)
        if create == bool(old):
            raise ValueError(
                "节点已存在，请使用 update_milestone"
                if create
                else "节点不存在，请使用 create_milestone"
            )
        if old and old.lease_active:
            raise ValueError("该节点已领取，请先释放后修改")
        architecture = p.architectures[-1] if p.architectures else None
        if set(proposed.architecture_components) - (
            {n.id for n in architecture.diagram.nodes} if architecture else set()
        ):
            raise ValueError("引用的架构组件不存在，请先更新架构设计")
        if architecture and not proposed.architecture_components:
            raise ValueError("请为里程碑关联至少一个架构组件")
        if set(proposed.attachment_ids) - {a.id for a in p.attachments}:
            raise ValueError("引用的资料不存在")
        before = [m.model_dump() for m in p.milestones]
        bids = []
        for behavior in proposed.behaviors:
            versions = [b for b in p.behaviors if b.behavior_key == behavior.key]
            latest = versions[-1] if versions else None
            if latest and latest.owner == proposed.id and latest.statement == behavior.statement:
                bids.append(latest.id)
            else:
                if (
                    latest
                    and latest.owner != proposed.id
                    and any(
                        latest.id in m.behavior_revision_ids
                        for m in p.milestones
                        if m.id != proposed.id
                    )
                ):
                    raise ValueError(f"行为 {behavior.key} 已归属 {latest.owner}，请先调整原节点")
                b = BehaviorRevision(
                    behavior_key=behavior.key,
                    statement=behavior.statement,
                    owner=proposed.id,
                    version=latest.version + 1 if latest else 1,
                    supersedes=latest.id if latest else None,
                )
                p.behaviors.append(b)
                bids.append(b.id)
        node = Milestone(
            **proposed.model_dump(exclude={"behaviors"}),
            behavior_revision_ids=bids,
            obligations=obligations(proposed.change_types),
            position=old.position if old else None,
            architecture_revision=architecture.number if architecture else 0,
        )
        if old:
            unchanged = all(
                getattr(old, f) == getattr(node, f)
                for f in [
                    "scope",
                    "architecture_components",
                    "resources",
                    "change_types",
                    "dependencies",
                    "behavior_revision_ids",
                ]
            )
            if unchanged:
                node.status, node.obligations, node.pinned_baseline = (
                    old.status,
                    old.obligations,
                    old.pinned_baseline,
                )
            node.dependency_types = {
                d: old.dependency_types.get(d, "implementation") for d in node.dependencies
            }
            p.milestones[p.milestones.index(old)] = node
        else:
            p.milestones.append(node)
        if architecture and not any(o.id == "architecture" for o in node.obligations):
            node.obligations.append(
                Obligation(
                    id="architecture", label=f"审查架构 A{architecture.number} 与当前里程碑的一致性"
                )
            )
        self._save(p, f"{'创建' if create else '更新'} {node.id} · {node.title}", before)
        return {"node_ids": [node.id], "effect": "created" if create else "updated"}

    def remove(self, project_id: str, milestone_id: str):
        p = self.db.get(project_id)
        m = p.milestone(milestone_id)
        if m.lease_active:
            raise ValueError("请先释放已领取的里程碑")
        dependents = [n.id for n in p.milestones if milestone_id in n.dependencies]
        if dependents:
            raise ValueError("请先通过 remove_dependency 调整后继依赖：" + ", ".join(dependents))
        before = [n.model_dump() for n in p.milestones]
        p.milestones.remove(m)
        self._save(p, f"移除 {milestone_id}", before)
        return {"node_ids": [milestone_id], "effect": "removed"}

    def dependency(
        self,
        project_id: str,
        source: str,
        target: str,
        reason: str = "",
        kind: str = "implementation",
        remove: bool = False,
    ):
        p = self.db.get(project_id)
        p.milestone(source)
        node = p.milestone(target)
        if node.lease_active:
            raise ValueError("目标节点正在执行，不能修改依赖")
        before = [n.model_dump() for n in p.milestones]
        if remove:
            node.dependencies = [d for d in node.dependencies if d != source]
            node.dependency_reasons.pop(source, None)
            node.dependency_types.pop(source, None)
        else:
            if not reason.strip():
                raise ValueError("必须提供前置依赖的理由")
            if source not in node.dependencies:
                node.dependencies.append(source)
            node.dependency_reasons[source], node.dependency_types[source] = reason, kind
        self._save(p, f"{'移除' if remove else '连接'} {source} → {target}", before)
        return {"node_ids": [target], "effect": "updated"}

    def target(self, project_id: str, statement: str):
        p = self.db.get(project_id)
        p.target_draft = statement
        self.db.save(p, "target_editing", statement)
        return {"node_ids": [], "effect": "target"}

    def finalize(self, project_id: str):
        p = self.db.get(project_id)
        required = [bid for m in p.milestones for bid in m.behavior_revision_ids]
        statement = p.target_draft or (
            p.targets[-1].statement if p.targets else p.description or p.name
        )
        if (
            not p.targets
            or required != p.targets[-1].required_behavior_ids
            or statement != p.targets[-1].statement
        ):
            p.targets.append(
                TargetVersion(
                    number=len(p.targets) + 1, statement=statement, required_behavior_ids=required
                )
            )
            if p.plans:
                p.plans[-1].target_version = p.targets[-1].number
            p.target_draft = None
            self.db.save(p, "target_committed", statement)
        elif p.target_draft is not None:
            p.target_draft = None
            self.db.save(p, "target_unchanged", statement)
