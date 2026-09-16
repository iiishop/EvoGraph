from typing import Literal

from pydantic import Field

from ..domain.models import Model, ProposedMilestone
from .base import tool


class NodeId(Model):
    milestone_id: str


class Dependency(Model):
    source: str
    target: str
    reason: str = Field(min_length=1)
    kind: Literal["implementation", "migration", "verification"] = "implementation"


class RemoveDependency(Model):
    source: str
    target: str


class Target(Model):
    statement: str = Field(min_length=1, max_length=4000)


@tool(
    "create_milestone",
    "Create one independently verifiable milestone in the current graph. Dependencies must already exist. Behavior keys must be unique across active nodes.",
    ProposedMilestone,
    label="创建里程碑",
    effect="created",
    focus_field="id",
)
def create_milestone(ctx, args):
    return ctx.application.graph.upsert(ctx.project_id, args, create=True)


@tool(
    "update_milestone",
    "Update an existing milestone in place. Supply the full node definition. Preserve stable id and unchanged behavior keys; changed behavior statements create revisions.",
    ProposedMilestone,
    label="更新里程碑",
    effect="updated",
    focus_field="id",
)
def update_milestone(ctx, args):
    return ctx.application.graph.upsert(ctx.project_id, args, create=False)


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
    "Add or update a prerequisite edge. Type is implementation, migration or verification; all edges mean prerequisite. Cycles are rejected.",
    Dependency,
    label="连接前置依赖",
    effect="updated",
    focus_field="target",
)
def add_dependency(ctx, args):
    return ctx.application.graph.dependency(ctx.project_id, **args.model_dump())


@tool(
    "remove_dependency",
    "Remove a prerequisite edge when it is no longer necessary.",
    RemoveDependency,
    label="移除依赖",
    effect="updated",
    focus_field="target",
)
def remove_dependency(ctx, args):
    return ctx.application.graph.dependency(ctx.project_id, **args.model_dump(), remove=True)


@tool(
    "set_target",
    "Set the current target statement when user intent changes. The final acceptance contract is derived from active behavior revisions at the end of this turn.",
    Target,
    label="更新目标",
    effect="target",
)
def set_target(ctx, args):
    return ctx.application.graph.target(ctx.project_id, args.statement)
