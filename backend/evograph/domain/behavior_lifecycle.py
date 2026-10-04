"""Current behavior membership is separate from append-only revision history."""

from .models import Project


def behavior_lifecycle(project: Project) -> dict:
    active_ids = {
        bid for node in project.milestones for bid in node.behavior_revision_ids
    }
    active_keys = {
        behavior.behavior_key for behavior in project.behaviors if behavior.id in active_ids
    }
    return {
        "basis": (
            "behaviors and targets include history. Only revisions referenced by CURRENT "
            "milestones are active; latest revision or acceptance_scope=target alone does not "
            "mean active or included in the latest committed target. Earlier target versions "
            "are history; the latest committed target may lag edits within this turn. "
            "Restoring an inactive behavior key changes current scope; it is not preservation."
        ),
        "latest_committed_target_version": project.targets[-1].number if project.targets else None,
        "active_revision_ids": [b.id for b in project.behaviors if b.id in active_ids],
        "historical_revision_ids": [b.id for b in project.behaviors if b.id not in active_ids],
        "inactive_behavior_keys": list(dict.fromkeys(
            b.behavior_key for b in project.behaviors if b.behavior_key not in active_keys
        )),
    }
