"""Deterministic DAG normalization, independent of the model and graph renderer."""


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
