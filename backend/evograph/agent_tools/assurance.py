from pydantic import Field

from ..domain.models import Model
from .base import tool


class MilestoneArgs(Model):
    milestone_id: str


class InvestigationArgs(MilestoneArgs):
    obligation_id: str
    paths: list[str] = Field(min_length=1, max_length=6)
    conclusion: str = Field(min_length=20, max_length=3000)


class CheckArgs(MilestoneArgs):
    candidate_id: str
    rationale: str = Field(min_length=20, max_length=2000)


class VisualArgs(MilestoneArgs):
    attachment_id: str
    findings: str = Field(min_length=20, max_length=4000)


@tool(
    "resolve_investigation",
    "Resolve an investigation using files read this turn. Explain concrete findings and remaining limits. If evidence is insufficient, ask_user instead.",
    InvestigationArgs,
    label="记录 Agent 调查依据",
    effect="updated",
    focus_field="milestone_id",
)
def investigate(ctx, args):
    return ctx.application.assurance.investigate(ctx, **args.model_dump())
