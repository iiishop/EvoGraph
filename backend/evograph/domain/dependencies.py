"""Deterministic DAG normalization, independent of the model and graph renderer."""

import json

MAX_PREVIEW_BYTES = 8192
MAX_PREVIEW_DEPENDENTS = 32


def ancestor_sets(dependencies: dict[str, list[str]]) -> dict[str, set[str]]:
    ancestors: dict[str, set[str]] = {}
    visiting: set[str] = set()

    def visit(node: str) -> set[str]:
        if node in ancestors:
            return ancestors[node]
        if node in visiting:
            raise ValueError("依赖图存在环，不能约简")
        if node not in dependencies:
            raise ValueError("依赖图引用不存在的节点：" + node)
        visiting.add(node)
        result: set[str] = set()
        for predecessor in dependencies[node]:
            result.add(predecessor)
            result.update(visit(predecessor))
        visiting.remove(node)
        ancestors[node] = result
        return result

    for node in dependencies:
        visit(node)
    return ancestors


def reduce_dependencies(milestones) -> list[dict[str, str]]:
    """Remove only edges with an alternative path; keep order and surviving metadata.

    Validate/compute the complete graph before mutating anything. Labels and edge
    kinds describe prerequisites; they do not exempt a redundant prerequisite.
    """
    graph = {m.id: list(m.dependencies) for m in milestones}
    ancestors = ancestor_sets(graph)
    removed = []
    for milestone in milestones:
        redundant = (
            set().union(*(ancestors[dep] for dep in graph[milestone.id]))
            if graph[milestone.id]
            else set()
        )
        for dep in graph[milestone.id]:
            if dep in redundant:
                removed.append(
                    {
                        "source": dep,
                        "target": milestone.id,
                        "reason": milestone.dependency_reasons.get(dep, ""),
                        "kind": milestone.dependency_types.get(dep, "implementation"),
                    }
                )
                milestone.dependency_reasons.pop(dep, None)
                milestone.dependency_types.pop(dep, None)
        milestone.dependencies = [dep for dep in graph[milestone.id] if dep not in redundant]
    return removed


def dependency_reduction_preview(milestones, project_revision):
    """Preview the existing finalizer on copies; never relabel projection as saved state."""
    milestones = list(milestones)
    result = {
        "status": "already_canonical",
        "project_revision": project_revision,
        "state": "projection_not_saved",
        "basis": "prerequisite_id -> dependent_id. Finalization removes only redundant direct "
        "edges, preserving prerequisite reachability. Projected direct prerequisites apply only "
        "if this graph is not edited again and finalization succeeds; current values are saved now.",
        "limits": {"bytes": MAX_PREVIEW_BYTES, "dependents": MAX_PREVIEW_DEPENDENTS},
        "removed_shortcut_count": 0,
        "affected_dependent_count": 0,
        "dependents": [],
        "omitted_dependent_count": 0,
        "truncated": False,
    }
    # Copy only graph nodes, not the project's behavior/evidence history.
    copies = [node.model_copy(deep=True) for node in milestones]
    try:
        removed = reduce_dependencies(copies)
    except (ValueError, RecursionError) as exc:
        return {**result, "status": "unavailable", "error": str(exc)[:500],
                "removed_shortcut_count": None, "affected_dependent_count": None,
                "omitted_dependent_count": None}
    if not removed:
        return result
    result["status"] = "reduction_projected"
    by_id = {node.id: node for node in copies}
    original = {node.id: node for node in milestones}
    shortcuts = {}
    for edge in removed:
        shortcuts.setdefault(edge["target"], []).append(edge["source"])
    result["removed_shortcut_count"] = len(removed)
    result["affected_dependent_count"] = len(shortcuts)
    for dependent in sorted(shortcuts):
        if len(result["dependents"]) >= MAX_PREVIEW_DEPENDENTS:
            break
        result["dependents"].append({
            "dependent_id": dependent,
            "current_direct_prerequisite_ids": list(original[dependent].dependencies),
            "projected_direct_prerequisite_ids": list(by_id[dependent].dependencies),
            "removed_direct_prerequisite_ids": shortcuts[dependent],
        })
        # Leave room for final counts. Whole IDs/rows are omitted, never shortened.
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_PREVIEW_BYTES - 256:
            result["dependents"].pop()
    result["omitted_dependent_count"] = len(shortcuts) - len(result["dependents"])
    result["truncated"] = result["omitted_dependent_count"] > 0
    return result
