from ..domain.design_review import review_design
from ..domain.models import Model
from .base import tool


class ReviewDesign(Model):
    pass


@tool(
    "review_design",
    "Review the CURRENT architecture and milestone plan without changing it. Returns structural findings with stable codes, repair guidance, revision and semantic review questions for the Agent (not questions for the user). Call after substantial design edits and again after repairs. An empty finding list is not acceptance or proof of semantic quality. Investigate advisory findings before acting; do not mechanically add complexity to silence them.",
    ReviewDesign,
    label="评审架构与里程碑设计",
)
def review(ctx, args):
    return review_design(ctx.application.db.get(ctx.project_id))
