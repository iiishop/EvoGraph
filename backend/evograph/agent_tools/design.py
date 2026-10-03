from ..domain.models import ArchitectureSpec, Diagram
from .base import tool


@tool(
    "update_architecture",
    "Maintain the project's versioned architecture, component boundaries, technology choices and "
    "decision rationale when architecture work is in scope and a new or changed design is needed. "
    "Do not call when the user explicitly excludes or defers architecture work; proceed with "
    "roadmap-only work and preserve existing architecture and diagrams. This tool is not a prerequisite "
    "for milestone planning or a way to silence advisory review findings. Existing component IDs are "
    "stable. Architecture changes require milestone review. Supply the complete intended summary, "
    "technologies and diagram. Omitted decisions, quality_scenarios, risks and research_ids retain "
    "their existing values; explicit lists replace them, including [] to clear. retirements is only "
    "this revision's removal delta. Preserve unrelated rationale and deliberately revise stale "
    "assumptions. Describe the known scale, constraints and proposed assumptions in summary. Choose "
    "a small number of semantic groups based on actual responsibilities or deployment/trust boundaries, "
    "not a mandatory layer taxonomy. Put each node in at most one group; logical groups do not imply "
    "separate services. Use a consistent abstraction level and cohesive responsibilities: identify "
    "public contracts, owned data/invariants and relevant failure handling. Label relationships with "
    "direction, protocol and purpose. Check a user journey and a likely change for missing ownership, "
    "tight coupling and redundant pass-through layers. Prefer the simplest viable design. Add a "
    "layer/interface/service only for a concrete project pressure; growth alone does not justify "
    "microservices. Patterns are optional: justify any added complexity against a simpler alternative "
    "in decisions, with tradeoffs and reconsideration triggers. Maintain relevant quality_scenarios "
    "with concrete stimulus, proposed measurable target and approach, never invented measurements. "
    "Record material risks/assumptions and mitigations. Inspect source and research version-sensitive "
    "facts before making consequential technology choices; distinguish source evidence from design.",
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
