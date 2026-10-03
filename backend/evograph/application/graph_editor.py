"""Incremental graph edits, checked and persisted before broadcasting to the UI."""

from ..domain.dependencies import ancestor_sets, reduce_dependencies
from ..domain.models import (
    BehaviorRevision,
    Milestone,
    Obligation,
    PlanningRevision,
    ProposedMilestone,
    TargetVersion,
)
from ..domain.policies import (
    MAX_WORKING_MILESTONES,
    obligations,
    resolve_architecture_components,
    validate_milestones,
)
from ..domain.target_contract import required_target_behavior_ids


class GraphEditor:
    def __init__(self, db):
        self.db = db

    def _check(self, project):
        if project.archived:
            raise ValueError("项目已删除")
        if len(project.milestones) > MAX_WORKING_MILESTONES:
            raise ValueError(f"工作图最多支持 {MAX_WORKING_MILESTONES} 个交付里程碑，请拆分项目")
        if not project.milestones:
            return
        by_id = {b.id: b for b in project.behaviors}
        source_ids = {m.id for m in project.source_milestones}
        proposed = []
        for node in project.milestones:
            behaviors = [by_id[bid] for bid in node.behavior_revision_ids]
            if any(b.owner != node.id for b in behaviors):
                raise ValueError("行为验收归属不一致")
            # Validate edge metadata before filtering out read-only SRC nodes.
            # Missing reasons must be a tool validation error, not a KeyError.
            if len(node.dependencies) != len(set(node.dependencies)):
                raise ValueError("同一前置依赖不能重复")
            if set(node.dependency_reasons) - set(node.dependencies):
                raise ValueError("依赖理由引用了不存在的连线")
            for dependency in node.dependencies:
                if not node.dependency_reasons.get(dependency, "").strip():
                    raise ValueError(f"{node.id} 的前置依赖 {dependency} 缺少依赖理由")
            dependencies = [d for d in node.dependencies if d not in source_ids]
            proposed.append(
                ProposedMilestone(
                    **node.model_dump(
                        include={
                            "id",
                            "title",
                            "intent",
                            "scope",
                            "architecture_components",
                            "resources",
                            "change_types",
                        }
                    ),
                    dependencies=dependencies,
                    dependency_reasons={d: node.dependency_reasons[d] for d in dependencies},
                    behaviors=[
                        {
                            "key": b.behavior_key,
                            "statement": b.statement,
                            "acceptance_scope": b.acceptance_scope,
                        }
                        for b in behaviors
                    ],
                )
            )
        validate_milestones(proposed)
        # SRC capabilities are read-only but remain real nodes in the complete
        # prerequisite graph used for cycle and reachability validation.
        ancestor_sets(
            {m.id: list(m.dependencies) for m in [*project.source_milestones, *project.milestones]}
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
        architecture_components = resolve_architecture_components(p, proposed, old)
        if set(proposed.attachment_ids) - {a.id for a in p.attachments}:
            raise ValueError("引用的资料不存在")
        dependencies = proposed.dependencies
        if old and "dependencies" not in proposed.model_fields_set:
            dependencies = old.dependencies
        dependency_reasons = proposed.dependency_reasons
        if old and "dependency_reasons" not in proposed.model_fields_set:
            # Retain metadata only for surviving edges. New prerequisites still
            # need an explicit reason, and [] deliberately removes every edge.
            dependency_reasons = {
                d: old.dependency_reasons[d] for d in dependencies if d in old.dependency_reasons
            }
        before = [m.model_dump() for m in p.milestones]
        bids = []
        existing_behaviors = {
            b.behavior_key: b for b in p.behaviors if old and b.id in old.behavior_revision_ids
        }
        for behavior in proposed.behaviors:
            scope = behavior.acceptance_scope
            if (
                "acceptance_scope" not in behavior.model_fields_set
                and behavior.key in existing_behaviors
            ):
                scope = existing_behaviors[behavior.key].acceptance_scope
            versions = [b for b in p.behaviors if b.behavior_key == behavior.key]
            latest = versions[-1] if versions else None
            if (
                latest
                and latest.owner == proposed.id
                and latest.statement == behavior.statement
                and latest.acceptance_scope == scope
            ):
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
                    acceptance_scope=scope,
                    owner=proposed.id,
                    version=latest.version + 1 if latest else 1,
                    supersedes=latest.id if latest else None,
                )
                p.behaviors.append(b)
                bids.append(b.id)
        node = Milestone(
            **proposed.model_dump(
                exclude={"behaviors", "architecture_components", "dependencies", "dependency_reasons"}
            ),
            dependencies=dependencies,
            dependency_reasons=dependency_reasons,
            architecture_components=architecture_components,
            behavior_revision_ids=bids,
            obligations=obligations(proposed.change_types),
            position=old.position if old else None,
            architecture_revision=architecture.number if architecture else 0,
        )
        if old:
            node.migration_steps = old.migration_steps
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
        return {
            "node_ids": [node.id],
            "effect": "created" if create else "updated",
            "dependency": {
                "dependent_id": node.id,
                "dependent_title": node.title,
                "previous_prerequisite_ids": list(old.dependencies) if old else [],
                "dependent_prerequisite_ids": list(node.dependencies),
                "dependency_reasons": dict(node.dependency_reasons),
                "dependency_types": {
                    d: node.dependency_types.get(d, "implementation") for d in node.dependencies
                },
                "state": "saved_before_transitive_reduction",
            },
        }

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
        if not any(m.id == source for m in p.source_milestones):
            p.milestone(source)
        elif remove:
            raise ValueError("SRC 基线能力不能通过计划工具修改")
        node = p.milestone(target)
        if node.lease_active:
            raise ValueError("目标节点正在执行，不能修改依赖")
        edge_existed_before = source in node.dependencies
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
        changed = before != [m.model_dump() for m in p.milestones]
        if changed:
            self._save(p, f"{'移除' if remove else '连接'} {source} → {target}", before)
        else:
            self._check(p)
        prerequisite = next(m for m in [*p.source_milestones, *p.milestones] if m.id == source)
        return {
            **({} if changed else {"status": "NO_PROGRESS"}),
            "node_ids": [target],
            "effect": "updated",
            "dependency": {
                "prerequisite_id": source,
                "prerequisite_title": prerequisite.title,
                "dependent_id": target,
                "dependent_title": node.title,
                "edge_existed_before": edge_existed_before,
                "edge_present_after": source in node.dependencies,
                "dependent_prerequisite_ids": list(node.dependencies),
                "state": "saved_before_transitive_reduction",
            },
        }

    def target(self, project_id: str, statement: str):
        p = self.db.get(project_id)
        p.target_draft = statement
        self.db.save(p, "target_editing", statement)
        return {"node_ids": [], "effect": "target"}

    def normalize(self, project_id: str):
        import json

        p = self.db.get(project_id)
        removed = reduce_dependencies([*p.source_milestones, *p.milestones])
        if removed:
            for milestone in p.milestones:
                milestone.position = None
            self.db.save(p, "dependencies_reduced", json.dumps(removed, ensure_ascii=False))
        return removed

    def finalize(self, project_id: str):
        removed = self.normalize(project_id)
        p = self.db.get(project_id)
        if not p.milestones and not p.targets and not p.target_draft:
            return removed
        required = required_target_behavior_ids(p.milestones, p.behaviors)
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
        return removed
