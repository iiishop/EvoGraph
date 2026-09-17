from ..domain.models import ArchitectureSpec, Diagram
from .base import tool


@tool(
    "update_architecture",
    "Maintain the project's versioned architecture, component boundaries, technology choices and decision rationale BEFORE drawing implementation milestones. Existing component IDs are stable. Architecture changes require milestone review.",
    ArchitectureSpec,
    label="更新架构与技术选型",
    effect="updated",
)
def update_architecture(ctx, args):
    return ctx.application.design.update(ctx.project_id, args)


@tool(
    "save_diagram",
    "Draw or update a structured visual explanation: state machine, UI structure, workflow. Cycles are allowed for state diagrams. Link existing uploaded images using attachment_ids and milestones using milestone_ids. Do not output executable HTML or SVG.",
    Diagram,
    label="绘制设计图",
    effect="updated",
)
def save_diagram(ctx, args):
    return ctx.application.design.save_diagram(ctx.project_id, args)
