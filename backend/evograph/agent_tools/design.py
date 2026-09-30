from ..domain.models import ArchitectureSpec, Diagram
from .base import tool


@tool(
    "update_architecture",
    "Maintain the project's versioned architecture, component boundaries, technology choices and decision rationale BEFORE drawing implementation milestones. Existing component IDs are stable. Architecture changes require milestone review. For architecture.diagram, choose a small number of semantic groups (usually client, server, data, external or shared) based on actual responsibilities. Put every architecture node in at most one group, use groups to make boundaries readable, and describe cross-group relations precisely. Do not create one group per node or group only by directory name. Design at a consistent abstraction level: describe responsibilities, interfaces and data ownership per component; label relationships with protocol, direction and purpose. Record alternatives and tradeoffs in decisions. Maintain quality_scenarios with concern, concrete scenario, measurable acceptance target and approach (reliability, security, performance and evolvability as relevant). State unconfirmed targets as proposals rather than measured facts. Record risks, assumptions and failure modes in risks; include mitigation. Investigate source and research before choosing technologies. Avoid speculative microservices or components without a concrete responsibility.",
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
