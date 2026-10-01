"""Derive final-goal membership without weakening individual milestone contracts."""

from .models import BehaviorRevision, Milestone


def required_target_behavior_ids(
    milestones: list[Milestone], behaviors: list[BehaviorRevision]
) -> list[str]:
    by_id = {behavior.id: behavior for behavior in behaviors}
    return [
        bid
        for milestone in milestones
        for bid in milestone.behavior_revision_ids
        if by_id[bid].acceptance_scope == "target"
    ]
