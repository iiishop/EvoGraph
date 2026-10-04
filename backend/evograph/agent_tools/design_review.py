from ..domain.change_context import change_context
from ..domain.dependencies import dependency_reduction_preview
from ..domain.design_review import review_design
from ..domain.models import Model
from .base import tool


class ReviewDesign(Model):
    pass


def current_review(ctx, project=None):
    project = project if project is not None else ctx.application.db.get(ctx.project_id)
    result = review_design(project)
    if ctx.review_context_revision == project.revision:
        result["change_context"] = {
            "status": "already_emitted", "project_revision": project.revision,
        }
        result["dependency_normalization"] = {
            "status": "already_emitted", "project_revision": project.revision,
        }
    else:
        result["change_context"] = change_context(ctx.before_snapshot, project)
        result["dependency_normalization"] = dependency_reduction_preview(
            [*project.source_milestones, *project.milestones], project.revision,
        )
        ctx.review_context_revision = project.revision
    return result


@tool(
    "review_design",
    "Review the CURRENT architecture and milestone plan without changing it. Returns structural "
    "findings with stable codes, repair guidance, revision and semantic review questions for the Agent "
    "(not questions for the user). prospective_target_membership previews actual final-goal counts "
    "from active behavior scopes, before turn finalization: a leaf or optional prose label does not "
    "exclude target-scoped requirements. target_finalization separates statement changes from "
    "required behavior revision-ID changes and reports the expected target version only if the "
    "reviewed state is not edited again and finalization succeeds; never report it as already "
    "committed or guaranteed. dependency_normalization distinguishes saved direct edges "
    "from projected final direct edges and retained prerequisite reachability; describe its outcome "
    "as conditional, not already saved. Call after substantial design edits and again after repairs. "
    "An empty finding list is not acceptance or proof of semantic quality. Investigate advisory "
    "findings before acting; do not mechanically add complexity to silence them.",
    ReviewDesign,
    label="评审架构与里程碑设计",
)
def review(ctx, args):
    return current_review(ctx)
