from pydantic import Field

from ..domain.models import LightCheck, Model
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


@tool(
    "discover_checks",
    "Discover registered lightweight checks from actual repository files. Read relevant test files before choosing 1–2 representative checks; do not invent shell commands.",
    MilestoneArgs,
    label="发现可用验收工具",
    focus_field="milestone_id",
)
def discover(ctx, args):
    return {"checks": ctx.application.assurance.catalog(ctx.project_id, args.milestone_id)}


@tool(
    "run_light_check",
    "Run a discovered check only in a user-authorized verification turn. Explain what behavior it covers and what it cannot establish. A passing light check is NOT full acceptance.",
    CheckArgs,
    label="执行代表性轻量检查",
    effect="updated",
    focus_field="milestone_id",
)
def run(ctx, args):
    return ctx.application.assurance.run(ctx, **args.model_dump())


@tool(
    "record_visual_review",
    "Record visual observations of an image actually attached to this turn with vision enabled. Describe visible defects, limitations and checked criteria. This is review evidence, never a test PASS.",
    VisualArgs,
    label="记录视觉检查",
    effect="updated",
    focus_field="milestone_id",
)
def visual(ctx, args):
    if args.attachment_id not in ctx.visual_attachments:
        raise ValueError("需要开启视觉能力，并将实际截图附加到本轮对话")
    p = ctx.application.db.get(ctx.project_id)
    p.milestone(args.milestone_id)
    if not p.baseline:
        raise ValueError("请先读取基线")
    p.light_checks.append(
        LightCheck(
            milestone_id=args.milestone_id,
            baseline_id=p.baseline.id,
            fingerprint=p.baseline.fingerprint,
            kind="visual:" + args.attachment_id,
            rationale="仅检查本轮上传图片中的可见内容，不代表运行时交互已验证",
            result="REVIEW",
            output=args.findings,
        )
    )
    ctx.application.db.save(p, "visual_review", args.findings)
    return {"node_ids": [args.milestone_id], "effect": "updated"}
