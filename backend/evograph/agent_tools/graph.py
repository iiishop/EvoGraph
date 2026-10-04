from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..domain.models import Model, ProposedMilestone
from .base import tool


class NodeId(Model):
    milestone_id: str


class DependencyEndpoints(Model):
    prerequisite_id: str = Field(
        description="ID of the prerequisite that must be satisfied BEFORE the dependent can proceed."
    )
    dependent_id: str = Field(
        description="ID of the milestone that NEEDS the prerequisite; its dependencies list is edited."
    )

    @model_validator(mode="before")
    @classmethod
    def legacy_endpoints(cls, value):
        # Old in-flight calls use source -> target. Never silently choose one
        # interpretation if both naming conventions are supplied.
        if isinstance(value, dict) and ({"source", "target"} & value.keys()):
            if {"prerequisite_id", "dependent_id"} & value.keys():
                raise ValueError("Use prerequisite_id and dependent_id together; do not mix endpoint names")
            if not {"source", "target"} <= value.keys():
                raise ValueError("Legacy endpoints require both source (prerequisite) and target (dependent)")
            value = dict(value)
            value["prerequisite_id"] = value.pop("source")
            value["dependent_id"] = value.pop("target")
        return value


class Dependency(DependencyEndpoints):
    reason: str = Field(min_length=1)
    kind: Literal["implementation", "migration", "verification"] = "implementation"


class RemoveDependency(DependencyEndpoints):
    pass


class Target(Model):
    statement: str = Field(min_length=1, max_length=4000)


class MilestoneEdit(ProposedMilestone):
    restore_inactive_behavior_keys: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=12,
        description=(
            "Explicitly acknowledge reintroducing these historical-only behavior keys. "
            "Use only when the CURRENT user goal or an explicit re-enable request supports "
            "restoration, never merely to clear a validation error. This is not proof of user "
            "authorization. Preserving current scope means retaining active behaviors, not "
            "restoring historical ones. No automatic user question is required. Specify the "
            "intended acceptance_scope to change it when restoring; omission preserves the "
            "latest historical revision's scope. Submitted already-active historical keys "
            "are allowed for safe retries and do not count as another restoration."
        ),
    )

    def definition(self):
        # Preserve nested field presence, especially omitted active scopes and
        # prerequisites. This tool-only acknowledgment is never persisted.
        return ProposedMilestone.model_validate(self.model_dump(
            exclude={"restore_inactive_behavior_keys"}, exclude_unset=True,
        ))


RESTORATION_GUIDANCE = (
    " Only revisions referenced by current milestones are active; the behaviors collection "
    "also contains history. Historical-only keys require restore_inactive_behavior_keys when "
    "the current user goal or explicit re-enable supports restoration. Do not mechanically "
    "add keys after an error or restore history while merely preserving current scope. "
    "The acknowledgment is not proof of user authorization. Check the factual "
    "restored_inactive_behaviors receipt and its saved scopes."
)


@tool(
    "create_milestone",
    "Create one independently verifiable milestone in the current graph. Dependencies must already exist. Behavior keys must be unique across active nodes. Set behavior acceptance_scope to target for requirements at completion of the current user-approved goal, including its exclusions, or milestone for local/transitional checks outside that goal's final contract. Deferred features stay excluded unless the user adds them. Both remain mandatory milestone acceptance; new behaviors default to target." + RESTORATION_GUIDANCE,
    MilestoneEdit,
    label="创建里程碑",
    effect="created",
    focus_field="id",
)
def create_milestone(ctx, args):
    return ctx.application.graph.upsert(
        ctx.project_id, args.definition(), create=True,
        restore_inactive_behavior_keys=args.restore_inactive_behavior_keys,
    )


@tool(
    "update_milestone",
    "Update an existing milestone in place. Supply the node's required definition fields. Omit dependencies to retain its current prerequisites; an explicit list replaces them, including [] to clear. Omit dependency_reasons to retain reasons for surviving edges; an explicit map replaces all reasons and must cover every prerequisite. New prerequisites need reasons. Existing edge types are retained. Check the receipt's saved prerequisites; turn finalization may remove transitively redundant edges. Preserve stable id and unchanged behavior keys; changed statements or acceptance_scope create revisions. Preserve existing scopes: omitted acceptance_scope retains the active behavior's scope, while new behaviors default to target. Both scopes remain mandatory milestone acceptance." + RESTORATION_GUIDANCE,
    MilestoneEdit,
    label="更新里程碑",
    effect="updated",
    focus_field="id",
)
def update_milestone(ctx, args):
    return ctx.application.graph.upsert(
        ctx.project_id, args.definition(), create=False,
        restore_inactive_behavior_keys=args.restore_inactive_behavior_keys,
    )


@tool(
    "remove_milestone",
    "Remove an unclaimed milestone. Remove outgoing dependency references first. Historical behavior/evidence records remain.",
    NodeId,
    label="移除里程碑",
    effect="removed",
    focus_field="milestone_id",
)
def remove_milestone(ctx, args):
    return ctx.application.graph.remove(ctx.project_id, args.milestone_id)


@tool(
    "add_dependency",
    "Add or update a blocking prerequisite. If B needs A first, use prerequisite_id=A and dependent_id=B: A is stored in B.dependencies, and the graph arrow is A -> B. Type explains why B needs A, never the direction. Cycles are rejected. The receipt reports the immediately saved state; turn finalization may remove transitively redundant edges.",
    Dependency,
    label="连接前置依赖",
    effect="updated",
    focus_field="dependent_id",
)
def add_dependency(ctx, args):
    return ctx.application.graph.dependency(
        ctx.project_id, source=args.prerequisite_id, target=args.dependent_id,
        reason=args.reason, kind=args.kind,
    )


@tool(
    "remove_dependency",
    "Remove a blocking prerequisite when it is no longer necessary. If B no longer needs A first, use prerequisite_id=A and dependent_id=B: remove A from B.dependencies (graph arrow A -> B). This does not remove either milestone.",
    RemoveDependency,
    label="移除依赖",
    effect="updated",
    focus_field="dependent_id",
)
def remove_dependency(ctx, args):
    return ctx.application.graph.dependency(
        ctx.project_id, source=args.prerequisite_id, target=args.dependent_id, remove=True,
    )


@tool(
    "set_target",
    "Set the current user-approved target statement when user intent changes. Deferred features stay excluded unless the user adds them. The final acceptance contract is derived only from active target-scope behavior revisions at the end of this turn. Milestone-scope checks remain mandatory for their own milestone but do not count toward the final goal.",
    Target,
    label="更新目标",
    effect="target",
)
def set_target(ctx, args):
    return ctx.application.graph.target(ctx.project_id, args.statement)
