import asyncio

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.repository import ReadFile, read_repository_file
from evograph.application.baseline_milestones import BaselineMilestones
from evograph.domain.models import Project, ProposedMilestone


def reconstruction(p, dependencies=None):
    graph = dependencies or {"SRC_login": []}
    return BaselineMilestones(
        baseline_id=p.baseline.id,
        summary="已检查登录实现；当前只提供固定返回值，未实现身份认证。",
        milestones=[
            dict(
                id=key,
                title=f"登录调用占位能力 {key}",
                intent="提供可调用的登录入口占位实现",
                scope=["auth.py"],
                dependencies=deps,
                dependency_reasons={d: "调用该前置能力" for d in deps},
                behaviors=[
                    dict(
                        key="login.stub", statement="调用 login 返回 True", source_refs=["auth.py"]
                    )
                ],
            )
            for key, deps in graph.items()
        ],
    )


def context(app, p):
    ctx = ToolContext(p.id, app)
    read_repository_file(ctx, ReadFile(path="auth.py"))
    return ctx


def test_reconstructs_contracts_without_acceptance_or_plan_mutations(app, planned):
    args = reconstruction(planned)
    ctx = context(app, planned)
    app.baseline_milestones.save(ctx, args)
    p = app.db.get(planned.id)
    assert p.milestones == planned.milestones
    assert p.behaviors == planned.behaviors
    assert p.targets == planned.targets
    assert p.evidence == []
    node = p.source_milestones[0]
    assert node.status == "IMPLEMENTED" and not node.lease_active
    assert node.source_behaviors[0].source_refs == ["auth.py"]
    assert node.source_baseline_id == p.baseline.id
    revision = p.revision
    assert app.baseline_milestones.save(ctx, args)["status"] == "NO_PROGRESS"
    assert app.db.get(p.id).revision == revision


def test_requires_reads_and_current_baseline_without_erasing_previous(app, planned, repository):
    args = reconstruction(planned)
    with pytest.raises(ValueError, match="实际读取"):
        app.baseline_milestones.save(ToolContext(planned.id, app), args)
    ctx = context(app, planned)
    app.baseline_milestones.save(ctx, args)
    (repository / "auth.py").write_text("def login(): return False\n")
    with pytest.raises(ValueError, match="发生变化"):
        app.baseline_milestones.save(ctx, args)
    new = app.execution.refresh(planned.id)
    assert new.source_milestones[0].source_baseline_id != new.baseline.id
    with pytest.raises(ValueError, match="基线已变化"):
        app.baseline_milestones.save(context(app, new), args)
    assert app.db.get(new.id).source_milestones == new.source_milestones


def test_reduces_dependencies_and_rejects_invalid_graph_atomically(app, planned):
    ctx = context(app, planned)
    args = reconstruction(planned, {"SRC_a": [], "SRC_b": ["SRC_a"], "SRC_c": ["SRC_a", "SRC_b"]})
    app.baseline_milestones.save(ctx, args)
    previous = app.db.get(planned.id).source_milestones
    assert previous[-1].dependencies == ["SRC_b"]
    assert previous[-1].dependency_reasons == {"SRC_b": "调用该前置能力"}
    for graph in ({"SRC_a": ["SRC_b"], "SRC_b": ["SRC_a"]}, {"SRC_a": ["missing"]}):
        with pytest.raises(ValueError):
            app.baseline_milestones.save(ctx, reconstruction(planned, graph))
        assert app.db.get(planned.id).source_milestones == previous


def test_replaces_snapshot_preserves_positions_and_drops_directory_legacy(app, planned):
    ctx = context(app, planned)
    app.baseline_milestones.save(ctx, reconstruction(planned, {"SRC_login": [], "SRC_old": []}))
    app.execution.positions(planned.id, {"SRC_login": {"x": 12, "y": 34}})
    app.baseline_milestones.save(ctx, reconstruction(planned))
    p = app.db.get(planned.id)
    assert [m.id for m in p.source_milestones] == ["SRC_login"]
    assert p.source_milestones[0].position == {"x": 12, "y": 34}
    raw = p.model_dump()
    raw["source_milestones"].append(dict(id="SRC_legacy", title="backend/src", intent="directory"))
    migrated = Project.model_validate(raw)
    assert len(migrated.source_milestones) == 1
    assert migrated.source_diagram == p.source_diagram


def test_streaming_tool_emits_graph_and_does_not_invent_goal(app, repository):
    from test_agent_stream import tool_chunks

    p = app.projects.create("Existing app", repository=str(repository))
    p = app.execution.refresh(p.id)
    calls = 0

    async def stream(messages, schemas):
        nonlocal calls
        calls += 1
        assert any(s["function"]["name"] == "reconstruct_baseline_milestones" for s in schemas)
        if calls == 1:
            name, args = "read_repository_file", {"path": "auth.py"}
        elif calls == 2:
            name, args = "reconstruct_baseline_milestones", reconstruction(p).model_dump()
        else:
            yield {"type": "text", "text": "done"}
            return
        async for event in tool_chunks(name, args):
            yield event

    app.settings.stream = stream

    async def run():
        return [e async for e in app.agent.stream(p.id, "倒推已实现里程碑")]

    events = asyncio.run(run())
    assert not [e for e in events if e["type"] in {"error", "tool_failed"}]
    assert any(e["type"] == "graph_changed" and e["node_ids"] == ["SRC_login"] for e in events)
    saved = app.db.get(p.id)
    assert not saved.targets and not saved.milestones and not saved.evidence
    assert saved.source_analysis_baseline_id == saved.baseline.id

    # Repeating the same reconstruction is a no-op, not grounds for a question.
    calls = 0
    repeated = asyncio.run(run())
    assert not [e for e in repeated if e["type"] in {"error", "question", "tool_failed"}]
    assert app.db.get(p.id).question is None


def test_source_reader_can_continue_long_implementation(app, planned, repository):
    content = "# implementation notes\n" * 350 + "def final_behavior(): return True\n"
    (repository / "auth.py").write_bytes(content.encode())
    ctx = ToolContext(planned.id, app)
    first = read_repository_file(ctx, ReadFile(path="auth.py"))
    assert first["truncated"] and "final_behavior" not in first["content"]
    second = read_repository_file(ctx, ReadFile(path="auth.py", offset=first["next_offset"]))
    assert "final_behavior" in second["content"] and second["next_offset"] is None
    assert first["content"] + second["content"] == content
    assert read_repository_file(ctx, ReadFile(path="auth.py"))["status"] == "NO_PROGRESS"


def test_source_capabilities_can_be_real_prerequisites_without_fake_acceptance(app, planned):
    ctx = context(app, planned)
    app.baseline_milestones.save(ctx, reconstruction(planned, {"SRC_login": []}))
    p = app.db.get(planned.id)
    app.graph.upsert(
        p.id,
        ProposedMilestone(
            id="M01",
            title="包装已有登录能力",
            intent="把现有能力接入新入口",
            scope=["auth.py"],
            dependencies=["SRC_login"],
            dependency_reasons={"SRC_login": "复用当前实现"},
            behaviors=[{"key": "auth.login", "statement": "入口可以调用登录能力"}],
        ),
        create=False,
    )
    app.graph.upsert(
        p.id,
        ProposedMilestone(
            id="M02",
            title="新登录入口",
            intent="增加新的登录入口",
            scope=["auth.py"],
            dependencies=["M01", "SRC_login"],
            dependency_reasons={"M01": "入口依赖包装层", "SRC_login": "直接复用现有能力"},
            behaviors=[{"key": "auth.entry", "statement": "新入口返回登录结果"}],
        ),
        create=True,
    )
    app.graph.finalize(p.id)
    p = app.db.get(p.id)
    assert p.milestone("M01").dependencies == ["SRC_login"]
    assert p.milestone("M02").dependencies == ["M01"]
    assert not any(
        "SRC_login" in blocker for blocker in app.projects.get(p.id)["readiness"]["M02"]["blockers"]
    )
