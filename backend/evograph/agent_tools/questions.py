from typing import Literal

from pydantic import Field

from ..domain.models import Model, PendingQuestion
from .base import tool


class AskUser(Model):
    prompt: str = Field(min_length=1, max_length=600)
    category: Literal["missing_design_input", "agent_blocked", "decision"]
    options: list[str] = Field(default_factory=list, max_length=5)
    context: str = Field(default="", max_length=1000)


@tool(
    "ask_user",
    "Ask the user only when one of three gates is true: missing design input that cannot be inferred, an agent-blocking fact unavailable in the repository/references, or a consequential route/technology/architecture decision. Never ask about routine implementation details, investigation work, or facts you can determine. Set category accordingly. The prompt must be one clear question only; keep context and options in their separate fields. Options are short answer labels, never embedded in prompt or context. Provide 2–5 mutually exclusive options only for a decision or bounded choice; free text remains available.",
    AskUser,
    label="等待你的回答",
    effect="question",
)
def ask_user(ctx, args):
    prompt = args.prompt.strip()
    if "?" not in prompt and "？" not in prompt:
        raise ValueError("问题必须是清晰的单一问句")
    if any(marker in prompt for marker in ["选项", "例如：", "比如：", "A:", "A：", "B:", "B："]):
        raise ValueError("问题正文不能包含选项或示例；请将选项放入 options")
    if args.options and "还是" in prompt:
        raise ValueError("问题正文不能内嵌二选一内容；请将答案放入 options")
    options = [item.strip() for item in args.options if item.strip()]
    if len(options) != len(set(options)):
        raise ValueError("选项不能重复")
    if args.category == "decision" and not (2 <= len(options) <= 5):
        raise ValueError("决策问题必须提供 2–5 个互斥选项")
    if args.category != "decision" and options and len(options) < 2:
        raise ValueError("提供选项时至少需要两个互斥选项")
    p = ctx.application.db.get(ctx.project_id)
    p.question = PendingQuestion(
        prompt=prompt, category=args.category, options=options, context=args.context.strip(),
        verification_milestone=ctx.verification_milestone
    )
    ctx.application.db.save(p, "agent_question", p.question.prompt)
    ctx.application.db.message(p.id, "assistant", p.question.prompt)
    ctx.paused = True
    return {"question": p.question.model_dump()}
