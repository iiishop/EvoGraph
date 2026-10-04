"""Incremental graph edits, checked and persisted before broadcasting to the UI."""

from ..domain.behavior_lifecycle import behavior_lifecycle
from ..domain.dependencies import reduce_dependencies
from ..domain.graph_validation import check_graph
from ..domain.models import (
    BehaviorRevision,
    Milestone,
    Obligation,
    PlanningRevision,
    ProposedMilestone,
    TargetVersion,
)
from ..domain.policies import (
    obligations,
    resolve_architecture_components,
)
from ..domain.target_contract import target_finalization


class GraphEditor:
    def __init__(self, db):
        self.db = db

    def _reusable_behavior_revision(self, active, latest):
        """Legacy editors reuse the latest history; atomic compilers may pin active IDs."""
        return latest

    def _check(self, project):
        check_graph(project)

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

    def upsert(
        self, project_id: str, proposed: ProposedMilestone, create: bool,
        *, restore_inactive_behavior_keys: list[str] | None = None,
    ):
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
        inactive_keys = set(behavior_lifecycle(p)["inactive_behavior_keys"])
        restoring = [b.key for b in proposed.behaviors if b.key in inactive_keys]
        # The model-facing tools require explicit lifecycle intent. None preserves
        # the existing lower-level API; the separate full-plan route is unchanged.
        if restore_inactive_behavior_keys is not None:
            submitted_keys = {b.key for b in proposed.behaviors}
            historical_keys = {b.behavior_key for b in p.behaviors}
            if len(restore_inactive_behavior_keys) != len(set(restore_inactive_behavior_keys)):
                raise ValueError("restore_inactive_behavior_keys 不能重复")
            if set(restore_inactive_behavior_keys) - submitted_keys:
                raise ValueError("restore_inactive_behavior_keys 只能引用本次提交的行为 key")
            if set(restore_inactive_behavior_keys) - historical_keys:
                raise ValueError("restore_inactive_behavior_keys 只能引用已有历史的行为 key")
            missing = set(restoring) - set(restore_inactive_behavior_keys)
            if missing:
                raise ValueError(
                    "这些行为仅在历史中，当前没有活跃引用：" + ", ".join(sorted(missing))
                    + "。保留当前范围时请从 behaviors 中省略它们；只有当前用户目标或明确重新启用"
                    "要求支持恢复时，才将对应 key 加入 restore_inactive_behavior_keys。"
                    "不要为消除报错机械添加标记；该标记不是用户授权证明，也不要求自动询问用户。"
                )
        previous_revisions = {
            b.behavior_key: b for b in p.behaviors if b.behavior_key in restoring
        }
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
            elif (
                "acceptance_scope" not in behavior.model_fields_set
                and behavior.key in previous_revisions
                and restore_inactive_behavior_keys is not None
            ):
                scope = previous_revisions[behavior.key].acceptance_scope
            versions = [b for b in p.behaviors if b.behavior_key == behavior.key]
            latest = versions[-1] if versions else None
            reusable = self._reusable_behavior_revision(existing_behaviors.get(behavior.key), latest)
            if (
                reusable
                and reusable.owner == proposed.id
                and reusable.statement == behavior.statement
                and reusable.acceptance_scope == scope
            ):
                bids.append(reusable.id)
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
            "restored_inactive_behaviors": [
                {
                    "behavior_key": b.behavior_key,
                    "previous_revision_id": previous_revisions[b.behavior_key].id,
                    "saved_revision_id": b.id,
                    "previous_acceptance_scope": previous_revisions[b.behavior_key].acceptance_scope,
                    "saved_acceptance_scope": b.acceptance_scope,
                    "state": "saved_active",
                }
                for b in p.behaviors if b.id in bids and b.behavior_key in previous_revisions
            ],
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
        # Both operations edit only the planned dependent's reference. A SRC
        # prerequisite stays read-only; milestone() still rejects SRC targets.
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
        decision = target_finalization(p)
        if decision is None:
            return removed
        if decision.creates_version:
            p.targets.append(
                TargetVersion(
                    number=decision.number, statement=decision.statement,
                    required_behavior_ids=decision.required_behavior_ids,
                )
            )
            if p.plans:
                p.plans[-1].target_version = p.targets[-1].number
            p.target_draft = None
            self.db.save(p, "target_committed", decision.statement)
        elif p.target_draft is not None:
            p.target_draft = None
            self.db.save(p, "target_unchanged", decision.statement)
        return removed
