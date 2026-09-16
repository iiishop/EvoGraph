from pydantic import Field

from ..domain.models import Model, PendingQuestion
from .base import tool


class AskUser(Model):
    prompt: str = Field(min_length=1, max_length=1500)
    options: list[str] = Field(default_factory=list, max_length=5)
    context: str = Field(default="", max_length=1500)


@tool(
    "ask_user",
    "Pause and ask a precise question when missing user intent blocks a reliable graph edit. Provide 2–5 short choices when helpful; free text is always available. Do not guess important requirements.",
    AskUser,
    label="等待你的回答",
    effect="question",
)
def ask_user(ctx, args):
    p = ctx.application.db.get(ctx.project_id)
    p.question = PendingQuestion(**args.model_dump())
    ctx.application.db.save(p, "agent_question", p.question.prompt)
    ctx.application.db.message(
        p.id, "assistant", p.question.prompt + "\n" + "\n".join(p.question.options)
    )
    ctx.paused = True
    return {"question": p.question.model_dump()}
