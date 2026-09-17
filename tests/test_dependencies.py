import asyncio
import random

import pytest
from conftest import proposal
from evograph.domain.dependencies import ancestor_sets, reduce_dependencies
from evograph.domain.models import Milestone
from evograph.domain.policies import readiness


def nodes(graph):
    return [
        Milestone(
            id=mid,
            title=mid,
            intent=mid,
            dependencies=deps,
            dependency_reasons={d: "reason " + d for d in deps},
            dependency_types={d: "migration" for d in deps},
        )
        for mid, deps in graph.items()
    ]


def test_chain_drops_shortcut_and_its_metadata():
    items = nodes({"M1": [], "M2": ["M1"], "M3": ["M1", "M2"]})
    removed = reduce_dependencies(items)
    assert [(r["source"], r["target"]) for r in removed] == [("M1", "M3")]
    assert items[-1].dependencies == ["M2"]
    assert items[-1].dependency_reasons == {"M2": "reason M2"}
    assert items[-1].dependency_types == {"M2": "migration"}
    assert reduce_dependencies(items) == []


def test_diamond_and_disconnected_nodes_keep_necessary_paths():
    items = nodes({"a": [], "b": ["a"], "c": ["a"], "d": ["a", "b", "c"], "x": []})
    reduce_dependencies(items)
    assert items[3].dependencies == ["b", "c"]
    assert items[1].dependencies == items[2].dependencies == ["a"]
    assert items[4].dependencies == []


def test_random_dags_preserve_all_ancestors_and_are_minimal():
    rng = random.Random(17)
    for size in range(2, 30):
        graph = {str(i): [str(j) for j in range(i) if rng.random() < 0.4] for i in range(size)}
        expected = ancestor_sets(graph)
        items = nodes(graph)
        reduce_dependencies(items)
        reduced = {m.id: m.dependencies for m in items}
        assert ancestor_sets(reduced) == expected
        for mid, deps in reduced.items():
            for dep in deps:
                without = {**reduced, mid: [d for d in deps if d != dep]}
                assert ancestor_sets(without) != expected


def test_invalid_graph_does_not_partially_mutate():
    items = nodes({"a": ["b"], "b": ["a"]})
    before = [m.model_dump() for m in items]
    with pytest.raises(ValueError, match="环"):
        reduce_dependencies(items)
    assert [m.model_dump() for m in items] == before


def test_agent_edges_are_accepted_then_reduced_on_finalize(app, planned):
    for mid in ["M02", "M03"]:
        node = proposal(node=mid, key=mid).milestones[0]
        app.graph.upsert(planned.id, node, True)
    app.graph.dependency(planned.id, "M01", "M02", "needs first")
    app.graph.dependency(planned.id, "M02", "M03", "needs second")
    app.graph.dependency(planned.id, "M01", "M03", "redundant but accepted")
    assert app.db.get(planned.id).milestone("M03").dependencies == ["M02", "M01"]
    removed = app.graph.finalize(planned.id)
    assert len(removed) == 1
    assert app.db.get(planned.id).milestone("M03").dependencies == ["M02"]
    revision = app.db.get(planned.id).revision
    app.graph.finalize(planned.id)
    assert app.db.get(planned.id).revision == revision


def test_readiness_still_checks_transitive_prerequisites(app, planned):
    p = app.db.get(planned.id)
    p.milestones.extend(nodes({"M02": ["M01"], "M03": ["M02"]}))
    # M02 has no additional contract in this fixture; the unmet ancestor still blocks M03.
    assert any("M01" in reason for reason in readiness(p, "M03")["blockers"])


def test_stream_emits_cleanup_notice_after_model_finishes(app, planned):
    from test_agent_stream import tool_chunks

    for mid in ["M02", "M03"]:
        app.graph.upsert(planned.id, proposal(node=mid, key=mid).milestones[0], True)
    edges = [("M01", "M02"), ("M02", "M03"), ("M01", "M03")]
    count = 0

    async def stream(messages, schemas):
        nonlocal count
        count += 1
        if count <= 3:
            source, target = edges[count - 1]
            async for e in tool_chunks(
                "add_dependency",
                {"source": source, "target": target, "reason": "fixture prerequisite"},
            ):
                yield e
        else:
            assert len(app.db.get(planned.id).milestone("M03").dependencies) == 2
            yield {"type": "text", "text": "完成"}

    app.settings.stream = stream

    async def collect():
        return [e async for e in app.agent.stream(planned.id, "连线")]

    events = asyncio.run(collect())
    assert any("自动移除 1 条" in e.get("message", "") for e in events)
    assert events[-1]["project"]["milestones"][-1]["dependencies"] == ["M02"]
