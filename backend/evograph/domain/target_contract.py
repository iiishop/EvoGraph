"""Derive final-goal membership and the read-only target finalization decision."""

from dataclasses import dataclass

from .models import BehaviorRevision, Milestone, Project


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


@dataclass(frozen=True)
class TargetFinalization:
    statement: str
    required_behavior_ids: list[str]
    number: int
    creates_version: bool
    statement_changed: bool
    required_behavior_ids_changed: bool


def target_finalization(project: Project) -> TargetFinalization | None:
    """Use the finalizer's exact defaults, ordering and comparison without saving.

    Missing active references still raise: finalization must not silently commit
    a partial contract. Review catches that error and withholds its projection.
    """
    if not project.milestones and not project.targets and not project.target_draft:
        return None
    required = required_target_behavior_ids(project.milestones, project.behaviors)
    current = project.targets[-1] if project.targets else None
    statement = project.target_draft or (
        current.statement if current else project.description or project.name
    )
    statement_changed = current is None or statement != current.statement
    required_changed = required != (current.required_behavior_ids if current else [])
    creates_version = current is None or statement_changed or required_changed
    return TargetFinalization(
        statement=statement,
        required_behavior_ids=required,
        number=len(project.targets) + 1 if creates_version else current.number,
        creates_version=creates_version,
        statement_changed=statement_changed,
        required_behavior_ids_changed=required_changed,
    )


def target_finalization_preview(project: Project) -> dict:
    """Compact review metadata, not a committed target or another state payload."""
    current = project.targets[-1] if project.targets else None
    result = {
        "status": "no_target",
        "project_revision": project.revision,
        "state": "projection_not_saved",
        "basis": "Only active planned target-scoped behavior revision IDs contribute, in their "
        "existing order. The expected target version applies only if this reviewed state is not "
        "edited again (including concurrent edits) and finalization succeeds; it is not committed "
        "or guaranteed. Statement and required-ID changes are separate; revision or order changes "
        "can create a version even when the statement is unchanged.",
        "latest_committed_target_version": current.number if current else None,
        "statement_changed": False,
        "required_behavior_ids_changed": False,
        "would_create_target_version": False,
        "expected_target_version_if_finalized": None,
        "committed_required_behavior_count": len(current.required_behavior_ids) if current else 0,
        "projected_required_behavior_count": 0,
    }
    try:
        decision = target_finalization(project)
    except KeyError:
        return {
            **result,
            "status": "unavailable",
            "error": "An active behavior reference is missing; target finalization cannot be projected.",
            "statement_changed": None,
            "required_behavior_ids_changed": None,
            "would_create_target_version": None,
            "projected_required_behavior_count": None,
        }
    if decision is None:
        return result
    return {
        **result,
        "status": "new_version_projected" if decision.creates_version else "unchanged",
        "statement_changed": decision.statement_changed,
        "required_behavior_ids_changed": decision.required_behavior_ids_changed,
        "would_create_target_version": decision.creates_version,
        "expected_target_version_if_finalized": decision.number,
        "projected_required_behavior_count": len(decision.required_behavior_ids),
    }
