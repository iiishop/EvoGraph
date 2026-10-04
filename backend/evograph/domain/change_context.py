"""Bounded saved facts for the planner's existing semantic review, never a verdict."""

import json
from collections import deque

from .models import Project

MAX_CONTEXT_BYTES = 32768
MAX_CONTEXT_NODES = 16
MAX_CONTEXT_CHANGES = 32
MAX_CONTEXT_DETAILS = 128
MAX_OMITTED_IDS = 32
_BUDGET = MAX_CONTEXT_BYTES - 2048  # Reserve space for final coverage/omission counts.
_CHANGE_BUDGET = 8192  # Keep large edge reasons from crowding out linked contracts.
_FIELDS = {
    "title", "intent", "scope", "architecture_components", "architecture_revision",
    "resources", "change_types", "status", "migration_steps", "origin",
    "source_baseline_id", "source_refs",
}


def _size(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _snapshot(project):
    nodes = {m.id: m for m in [*project.milestones, *project.source_milestones]}
    behaviors = {b.id: b for b in project.behaviors}
    contracts, edges, downstream = {}, {}, {mid: [] for mid in nodes}
    for mid, node in nodes.items():
        item = node.model_dump(include=_FIELDS)
        item["prerequisites"] = []
        for prerequisite in node.dependencies:
            edge = {
                "prerequisite_id": prerequisite, "dependent_id": mid,
                "reason": node.dependency_reasons.get(prerequisite, ""),
                "kind": node.dependency_types.get(prerequisite, "implementation"),
            }
            edges[prerequisite, mid] = edge
            item["prerequisites"].append(edge)
            downstream.setdefault(prerequisite, []).append(mid)
        if node.origin == "source":
            item["source_behaviors"] = [b.model_dump() for b in node.source_behaviors]
        else:
            item["active_behaviors"], item["unresolved_behavior_references"] = [], []
            for bid in node.behavior_revision_ids:
                behavior = behaviors.get(bid)
                if behavior is not None and behavior.owner == mid:
                    item["active_behaviors"].append(behavior.model_dump())
                else:
                    item["unresolved_behavior_references"].append({
                        "id": bid, "owner": behavior.owner if behavior else None,
                    })
        contracts[mid] = item
    return nodes, contracts, edges, downstream


def _walk(seeds, adjacency):
    """Breadth-first order with stable ties; each snapshot/direction stays separate."""
    queue, seen = deque(sorted(seeds)), set(seeds)
    while queue:
        node = queue.popleft()
        yield node
        for neighbor in sorted(adjacency.get(node, [])):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)


def _summary(contract):
    if contract is None:
        return None
    summary = {key: contract[key] for key in (
        "title", "origin", "status", "architecture_revision", "architecture_components",
        "source_baseline_id", "unresolved_behavior_references",
    ) if key in contract}
    summary["prerequisite_ids"] = [edge["prerequisite_id"] for edge in contract["prerequisites"]]
    if "active_behaviors" in contract:
        summary["active_behavior_revision_ids"] = [b["id"] for b in contract["active_behaviors"]]
    if "source_behaviors" in contract:
        summary["source_behavior_keys"] = [b["key"] for b in contract["source_behaviors"]]
    return summary


def _before_values(item, before, current):
    """Absent fields differ from present null; unchanged values are not repeated."""
    if before is None or current is None:
        item["before"] = before
    elif before != current:
        item["before_overrides"] = {
            key: value for key, value in before.items()
            if key not in current or current[key] != value
        }
        item["before_absent_fields"] = sorted(current.keys() - before.keys())


def _contracts(mid, before, current):
    rows = []
    for field, kind, key_field in (
        ("active_behaviors", "behavior_revision", "behavior_key"),
        ("source_behaviors", "source_observation", "key"),
    ):
        old, new = (before or {}).get(field, []), (current or {}).get(field, [])
        # A malformed duplicate key must not silently discard a saved contract.
        keys = list(dict.fromkeys(b[key_field] for b in [*new, *old]))
        for key in keys:
            left = [b for b in old if b[key_field] == key]
            right = [b for b in new if b[key_field] == key]
            pairs = []
            # Match exact records (including duplicate occurrences), then actual
            # revision identities. Duplicate keys alone never invent replacement.
            for same_record in (True, False):
                for previous in list(left):
                    if same_record:
                        match = next((b for b in right if b == previous), None)
                    else:
                        match = next((b for b in right if kind == "behavior_revision"
                                      and b["id"] == previous["id"]), None)
                    if match is not None:
                        pairs.append((previous, match))
                        left.remove(previous)
                        right.remove(match)
            if len(left) <= 1 and len(right) <= 1:
                if left or right:
                    pairs.append((left[0] if left else None, right[0] if right else None))
            else:
                pairs.extend((b, None) for b in left)
                pairs.extend((None, b) for b in right)
            for previous, saved in pairs:
                row = {"node_id": mid, "kind": kind, "key": key, "current": saved,
                       "change": "added" if previous is None else "removed" if saved is None
                       else "unchanged" if previous == saved else "updated"}
                if previous != saved:
                    row["before"] = previous
                rows.append(row)

    def priority(row):
        old, new = row.get("before"), row["current"]
        if old and new and old.get("acceptance_scope") != new.get("acceptance_scope"):
            return 0
        if old and new and old["statement"] != new["statement"]:
            return 1
        return 3 if row["change"] == "unchanged" else 2

    return sorted(rows, key=priority)


def _round_robin(groups):
    queues = [deque(group) for group in groups if group]
    while queues:
        for queue in queues:
            yield queue.popleft()
        queues = [queue for queue in queues if queue]


def change_context(before: Project | None, current: Project):
    """Summaries first; whole contract/field records never imply complete coverage."""
    result = {
        "status": "no_turn_start_snapshot" if before is None else "unchanged",
        "before_revision": before.revision if before else None,
        "project_revision": current.revision,
        "basis": "Saved milestone/dependency facts; prerequisite_id -> dependent_id. Both acceptance "
        "scopes require milestone acceptance; only target contributes to the final goal. SRC "
        "contracts are source inferences, not verified acceptance. No semantic verdict is implied.",
        "encoding": "nodes contain summaries and stable contract references, not full contracts. "
        "For an existing summary, reconstruct before by applying before_overrides to current and "
        "removing before_absent_fields; absent overrides mean unchanged. Explicit before/current "
        "null denotes node absence. contracts and node_fields contain whole selected details; "
        "node_fields before_present/current_present distinguish absent fields from present null, "
        "and an omitted before value when both are true equals current. omitted_contract_count "
        "and omitted_fields describe missing details for each included node; stable before/current "
        "revision IDs or source keys identify contract references even when their detail is omitted. "
        "Duplicate source keys retain multiplicity but do not identify individual occurrences.",
        "state": "saved_before_transitive_reduction",
        "limits": {"bytes": MAX_CONTEXT_BYTES, "nodes": MAX_CONTEXT_NODES,
                   "changes_per_kind": MAX_CONTEXT_CHANGES, "details_per_kind": MAX_CONTEXT_DETAILS},
        "milestone_changes": [], "dependency_changes": [], "nodes": [],
        "contracts": [], "node_fields": [],
        "coverage": {"eligible_node_count": 0, "omitted_node_count": 0,
                     "omitted_node_ids": [], "unlisted_omitted_node_count": 0,
                     "omitted_milestone_change_count": 0, "omitted_dependency_change_count": 0,
                     "detail_scope": "included_node_summaries", "omitted_contract_count": 0,
                     "omitted_field_count": 0},
        "truncated": False,
        "current_state_tool": "read_project",
        "historical_omission_limit": "read_project does not recover omitted before-state records.",
    }
    if before is None:
        return result
    old_nodes, old, old_edges, old_downstream = _snapshot(before)
    new_nodes, new, new_edges, new_downstream = _snapshot(current)
    changed = sorted(mid for mid in old.keys() | new.keys() if old.get(mid) != new.get(mid))
    edge_keys = sorted(key for key in old_edges.keys() | new_edges.keys()
                       if old_edges.get(key) != new_edges.get(key))
    if not changed and not edge_keys:
        return result
    result["status"] = "changed"

    def append(field, item, limit, budget=_BUDGET):
        if len(result[field]) >= limit:
            return False
        result[field].append(item)
        if _size(result) > budget:
            result[field].pop()
            return False
        return True

    endpoints = sorted({mid for edge in edge_keys for mid in edge})
    priority = list(dict.fromkeys([*changed, *endpoints]))
    seeds = set(changed)
    walks = [
        _walk(seeds, {mid: node.dependencies for mid, node in old_nodes.items()}),
        _walk(seeds, {mid: node.dependencies for mid, node in new_nodes.items()}),
        _walk(seeds, old_downstream), _walk(seeds, new_downstream),
    ]
    priority.extend(_round_robin(walks))
    eligible = list(dict.fromkeys(mid for mid in priority if mid in old or mid in new))
    included, contract_groups, field_groups = {}, [], []
    summary_fields = {"title", "origin", "status", "architecture_revision", "architecture_components",
                      "source_baseline_id", "unresolved_behavior_references"}
    for mid in eligible:
        previous, saved = old.get(mid), new.get(mid)
        contracts = _contracts(mid, previous, saved)
        fields = []
        for key in sorted((previous or {}).keys() | (saved or {}).keys()):
            if key in summary_fields or key in {"active_behaviors", "source_behaviors"}:
                continue
            old_present, new_present = key in (previous or {}), key in (saved or {})
            row = {"node_id": mid, "field": key,
                   "before_present": old_present, "current_present": new_present}
            if new_present:
                row["current"] = saved[key]
            if old_present and (not new_present or previous[key] != saved[key]):
                row["before"] = previous[key]
            fields.append(row)
        item = {"id": mid, "current": _summary(saved), "omitted_contract_count": len(contracts),
                "omitted_fields": [row["field"] for row in fields]}
        _before_values(item, _summary(previous), item["current"])
        if append("nodes", item, MAX_CONTEXT_NODES):
            included[mid] = item
            contract_groups.append(contracts)
            field_groups.append(fields)

    # Every admitted node gets its summary before any large contract consumes space.
    # Deltas retain an independent allowance without crowding out contract details.
    change_budget = min(_BUDGET, _size(result) + _CHANGE_BUDGET)
    for mid in changed:
        fields = sorted(key for key in old.get(mid, {}).keys() | new.get(mid, {}).keys()
                        if key not in old.get(mid, {}) or key not in new.get(mid, {})
                        or old[mid][key] != new[mid][key])
        append("milestone_changes", {
            "id": mid, "change": "added" if mid not in old else "removed" if mid not in new
            else "updated", "fields": fields,
        }, MAX_CONTEXT_CHANGES, change_budget)
    for prerequisite, dependent in edge_keys:
        append("dependency_changes", {
            "prerequisite_id": prerequisite, "dependent_id": dependent,
            "before": old_edges.get((prerequisite, dependent)),
            "current": new_edges.get((prerequisite, dependent)),
        }, MAX_CONTEXT_CHANGES, change_budget)

    # First offer one complete contract per node, then changed acceptance/statements
    # before unchanged contracts, without letting one verbose owner monopolize detail.
    first = [group[:1] for group in contract_groups]
    changed_contracts = [[row for row in group[1:] if row["change"] != "unchanged"]
                         for group in contract_groups]
    unchanged_contracts = [[row for row in group[1:] if row["change"] == "unchanged"]
                           for group in contract_groups]
    for groups in (first, changed_contracts):
        for row in _round_robin(groups):
            if append("contracts", row, MAX_CONTEXT_DETAILS):
                included[row["node_id"]]["omitted_contract_count"] -= 1
    # Changed scope/intent follows changed behavior, before extra unchanged data.
    changed_fields = [[row for row in group if "before" in row
                       or row["before_present"] != row["current_present"]] for group in field_groups]
    unchanged_fields = [[row for row in group if row not in changed]
                        for group, changed in zip(field_groups, changed_fields, strict=True)]
    for field, groups in (("node_fields", changed_fields), ("contracts", unchanged_contracts),
                          ("node_fields", unchanged_fields)):
        for row in _round_robin(groups):
            if append(field, row, MAX_CONTEXT_DETAILS):
                if field == "contracts":
                    included[row["node_id"]]["omitted_contract_count"] -= 1
                else:
                    included[row["node_id"]]["omitted_fields"].remove(row["field"])

    omitted = [mid for mid in eligible if mid not in included]
    coverage = result["coverage"]
    coverage.update({
        "eligible_node_count": len(eligible), "omitted_node_count": len(omitted),
        "omitted_node_ids": omitted[:MAX_OMITTED_IDS],
        "unlisted_omitted_node_count": max(0, len(omitted) - MAX_OMITTED_IDS),
        "omitted_milestone_change_count": len(changed) - len(result["milestone_changes"]),
        "omitted_dependency_change_count": len(edge_keys) - len(result["dependency_changes"]),
        "omitted_contract_count": sum(row["omitted_contract_count"] for row in result["nodes"]),
        "omitted_field_count": sum(len(row["omitted_fields"]) for row in result["nodes"]),
    })
    while _size(result) > MAX_CONTEXT_BYTES and coverage["omitted_node_ids"]:
        coverage["omitted_node_ids"].pop()
        coverage["unlisted_omitted_node_count"] += 1
    result["truncated"] = any(value for key, value in coverage.items()
                              if key.startswith("omitted_") and isinstance(value, int))
    return result
