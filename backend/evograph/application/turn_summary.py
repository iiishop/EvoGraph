"""Factual, bounded turn outcomes derived from committed planning snapshots.

History, model narration, layout, and accounting never determine whether a turn
changed the plan. The event stores identifiers and field names, not snapshots or model judgments.
"""

from typing import Literal

from ..domain.models import Project

TurnStatus = Literal["completed", "waiting", "stopped", "failed"]
_EDGE_FIELDS = {"dependencies", "dependency_reasons", "dependency_types"}


def _milestones(project: Project) -> dict:
    return {
        milestone.id: milestone.model_dump(exclude={"position", *_EDGE_FIELDS})
        for milestone in [*project.source_milestones, *project.milestones]
    }


def _milestone_changes(before: Project, after: Project) -> dict:
    old, new = _milestones(before), _milestones(after)
    changes = {"added": [], "updated": [], "removed": []}
    for mid in sorted(old.keys() | new.keys()):
        node = new.get(mid, old.get(mid))
        item = {"id": mid, "title": node["title"], "fields": []}
        if mid not in old:
            changes["added"].append(item)
        elif mid not in new:
            changes["removed"].append(item)
        else:
            item["fields"] = sorted(key for key in old[mid] if old[mid][key] != new[mid][key])
            if item["fields"]:
                changes["updated"].append(item)
    return changes


def _edges(project: Project) -> dict:
    return {
        (source, milestone.id): {
            "reason": milestone.dependency_reasons.get(source, ""),
            "type": milestone.dependency_types.get(source, "implementation"),
        }
        for milestone in [*project.source_milestones, *project.milestones]
        for source in milestone.dependencies
    }


def _dependency_changes(before: Project, after: Project) -> dict:
    old, new = _edges(before), _edges(after)
    changes = {"added": [], "updated": [], "removed": []}
    for source, target in sorted(old.keys() | new.keys()):
        key = (source, target)
        item = {"source": source, "target": target}
        if key not in old:
            changes["added"].append({**item, **new[key]})
        elif key not in new:
            changes["removed"].append({**item, **old[key]})
        elif old[key] != new[key]:
            changes["updated"].append(
                {
                    **item,
                    "fields": sorted(
                        field for field in old[key] if old[key][field] != new[key][field]
                    ),
                    "before": old[key],
                    "after": new[key],
                }
            )
    return changes


def _behavior_maps(project: Project) -> tuple[dict, dict]:
    by_id = {behavior.id: behavior for behavior in project.behaviors}
    required = project.targets[-1].required_behavior_ids if project.targets else []
    target = {by_id[bid].behavior_key: by_id[bid] for bid in required if bid in by_id}
    active = {
        by_id[bid].behavior_key: by_id[bid]
        for milestone in project.milestones
        for bid in milestone.behavior_revision_ids
        if bid in by_id
    }
    return target, active


def _target_changes(before: Project, after: Project) -> dict | None:
    old = before.targets[-1] if before.targets else None
    new = after.targets[-1] if after.targets else None
    old_ids = set(old.required_behavior_ids if old else [])
    new_ids = set(new.required_behavior_ids if new else [])
    statement_changed = (old.statement if old else None) != (new.statement if new else None)
    if not statement_changed and old_ids == new_ids:
        return None
    old_target, old_active = _behavior_maps(before)
    new_target, new_active = _behavior_maps(after)
    behaviors = []
    for key in sorted(old_target.keys() | new_target.keys()):
        old_id = old_target[key].id if key in old_target else None
        new_id = new_target[key].id if key in new_target else None
        if old_id == new_id:
            continue
        # Membership IDs remain null outside the target. Active contracts still
        # tell a scope flip apart from a newly added/removed semantic behavior.
        left = old_active.get(key, old_target.get(key))
        right = new_active.get(key, new_target.get(key))
        fields = [
            field
            for field in ("statement", "acceptance_scope", "owner")
            if left and right and getattr(left, field) != getattr(right, field)
        ]
        behaviors.append(
            {"behavior_key": key, "before_id": old_id, "after_id": new_id, "fields": fields}
        )
    return {
        "before_version": old.number if old else None,
        "after_version": new.number if new else None,
        "statement_changed": statement_changed,
        "required_behavior_ids": {
            "added": sorted(new_ids - old_ids),
            "removed": sorted(old_ids - new_ids),
        },
        "required_behavior_changes": behaviors,
    }


def _contract_details(before: Project, after: Project) -> dict:
    """Keep active references distinct from committed-target membership.

    Immutable history supplies the text. Missing records retain their exact IDs,
    rather than borrowing a newer revision with the same key. The small receipt
    also binds unchanged target references to this turn, not today's target.
    """
    def references(project: Project) -> dict:
        by_id = {item.id: item for item in project.behaviors}
        active = {bid for node in project.milestones for bid in node.behavior_revision_ids}
        required = set(project.targets[-1].required_behavior_ids if project.targets else [])
        result = {}
        for bid in sorted(active | required):
            behavior = by_id.get(bid)
            key = ("key", behavior.behavior_key) if behavior else ("missing", bid)
            side = result.setdefault(key, {"active_id": None, "required_id": None})
            if bid in active:
                side["active_id"] = bid
            if bid in required:
                side["required_id"] = bid
        return result

    old, new = references(before), references(after)
    old_records = {item.id: item for item in before.behaviors}
    new_records = {item.id: item for item in after.behaviors}
    old_keys = {item.behavior_key for item in before.behaviors}
    empty = {"active_id": None, "required_id": None}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        left, right = old.get(key, empty), new.get(key, empty)
        if left == right:
            continue
        previous = old_records.get(left["active_id"] or left["required_id"])
        current = new_records.get(right["active_id"] or right["required_id"])
        changes.append({
            "behavior_key": key[1] if key[0] == "key" else None,
            "before": left,
            "after": right,
            "fields": [
                field for field in ("statement", "acceptance_scope", "owner")
                if previous and current and getattr(previous, field) != getattr(current, field)
            ],
            "restored": bool(
                not left["active_id"] and right["active_id"]
                and key[0] == "key" and key[1] in old_keys
            ),
        })
    return {
        "project_id": before.id,
        "project_created_at": before.created_at,
        "before_target_version": before.targets[-1].number if before.targets else None,
        "after_target_version": after.targets[-1].number if after.targets else None,
        "behaviors": changes,
    }


def _other_areas(project: Project) -> dict:
    return {
        "project": {key: getattr(project, key) for key in ("name", "description", "repository")},
        "baselines": project.baseline.model_dump(exclude={"created_at"})
        if project.baseline
        else None,
        "diagrams": {item.id: item.model_dump() for item in project.diagrams},
        "uml_diagrams": {
            item.id: item.model_dump(exclude={"revision"}) for item in project.uml_diagrams
        },
        "attachments": {
            item.id: item.model_dump(exclude={"created_at"}) for item in project.attachments
        },
        "research": {item.id: item.model_dump(exclude={"created_at"}) for item in project.research},
        "source_analysis": project.model_dump(
            include={
                "source_diagram",
                "source_summary",
                "source_fingerprint",
                "source_file_fingerprints",
                "source_analysis_baseline_id",
                "source_analysis_summary",
            }
        ),
        "evidence": {item.id: item.model_dump(exclude={"created_at"}) for item in project.evidence},
        "light_checks": {
            item.id: item.model_dump(exclude={"created_at"}) for item in project.light_checks
        },
    }


def build_turn_summary(before: Project, after: Project, turn_id: str, status: TurnStatus) -> dict:
    """Compare persisted snapshots; revision numbers refer to those actual reads."""
    old_architecture = before.architectures[-1] if before.architectures else None
    new_architecture = after.architectures[-1] if after.architectures else None
    architecture = None
    old_design = (
        old_architecture.model_dump(exclude={"number", "created_at"}) if old_architecture else None
    )
    new_design = (
        new_architecture.model_dump(exclude={"number", "created_at"}) if new_architecture else None
    )
    if old_design != new_design:
        architecture = {
            "before_revision": old_architecture.number if old_architecture else None,
            "after_revision": new_architecture.number if new_architecture else None,
        }
    old_areas, new_areas = _other_areas(before), _other_areas(after)
    other = [key for key in old_areas if old_areas[key] != new_areas[key]]
    # A failed finalize can leave an actual saved draft without changing the
    # committed target. Successful draft clearing is covered by that contract.
    if after.target_draft is not None and before.target_draft != after.target_draft:
        other.append("target_draft")
    changes = {
        "milestones": _milestone_changes(before, after),
        "dependencies": _dependency_changes(before, after),
        "target": _target_changes(before, after),
        "architecture": architecture,
        "other": sorted(other),
    }
    contract_details = _contract_details(before, after)
    return {
        "version": 1,
        "contract_details": contract_details,
        "turn_id": turn_id,
        "status": status,
        "changed": bool(
            any(changes["milestones"].values())
            or any(changes["dependencies"].values())
            or changes["target"]
            or architecture
            or changes["other"]
            or contract_details["behaviors"]
        ),
        "before_revision": before.revision,
        "after_revision": after.revision,
        "changes": changes,
    }


def read_turn_summary(detail: str, turn_id: str) -> dict | None:
    """Ignore legacy free-text outcomes and unknown future versions safely."""
    import json

    try:
        summary = json.loads(detail)
    except (TypeError, ValueError):
        return None
    if (
        isinstance(summary, dict)
        and summary.get("version") == 1
        and summary.get("turn_id") == turn_id
        and isinstance(summary.get("status"), str)
        and summary.get("status") in {"completed", "waiting", "stopped", "failed"}
        and isinstance(summary.get("changes"), dict)
        and isinstance(summary.get("changed"), bool)
    ):
        return summary
    return None
