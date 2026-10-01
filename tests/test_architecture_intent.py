"""Provider-visible intent contracts and persistence, not a model-obedience evaluation."""

import asyncio
import json
from copy import deepcopy

import pytest
from conftest import apply_proposal, proposal
from evograph.application.design_workflow import ARCHITECTURE_INTENT
from evograph.domain.models import ArchitectureSpec, Diagram, ProposedMilestone


def architecture_spec(summary="Keep the existing API boundary"):
    return ArchitectureSpec(
        summary=summary,
        technologies=[{"area": "API", "choice": "Python", "rationale": "Existing stack"}],
        decisions=["Keep storage isolated"],
        diagram=Diagram(
            id="system",
            title="System",
            nodes=[{"id": "api", "label": "API", "description": "Own the login contract"}],
        ),
    )


def seed_architecture(app, project_id):
    app.design.update(project_id, architecture_spec("Historical architecture"))
    app.design.update(project_id, architecture_spec())
    app.design.save_diagram(
        project_id,
        Diagram(
            id="login-flow",
            title="Existing login flow",
            kind="workflow",
            nodes=[{"id": "login", "label": "Login"}],
        ),
    )


def chunk(name, arguments):
    return {
        "type": "tool_delta",
        "index": 0,
        "id": "call",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


@pytest.mark.parametrize("existing", [False, True], ids=["no-architecture", "preserve-existing"])
@pytest.mark.parametrize(
    "user_request",
    ["只规划登录的 PR 路线图，不用架构图", "Plan the login roadmap; architecture later"],
    ids=["excluded", "deferred"],
)
def test_roadmap_only_stream_keeps_architecture_optional_through_review(
    app, existing, user_request
):
    project = app.projects.create("Login roadmap")
    node = proposal().milestones[0].model_dump()
    if existing:
        seed_architecture(app, project.id)
        node["architecture_components"] = ["api"]
        app.graph.upsert(project.id, ProposedMilestone.model_validate(node), create=True)
        app.graph.finalize(project.id)
    before = app.db.get(project.id)
    node["title"] = "Refined login roadmap"
    captured = []

    async def stream(messages, schemas):
        captured.append((deepcopy(messages), deepcopy(schemas)))
        if len(captured) == 1:
            yield chunk("update_milestone" if existing else "create_milestone", node)
        elif len(captured) == 3:
            yield chunk("review_design", {})
        else:
            yield {"type": "text", "text": "路线图已更新"}

    app.settings.stream = stream

    async def run():
        return [event async for event in app.agent.stream(project.id, user_request)]

    events = asyncio.run(run())
    assert not [event for event in events if event["type"] in {"error", "tool_failed", "question"}]
    assert len(captured) == 4  # Includes the runtime's mandatory post-edit review reminder.
    messages, schemas = captured[0]
    system = messages[0]["content"]
    assert ARCHITECTURE_INTENT in system
    assert "Skipping architecture work is not a request to delete it" in system
    assert "Missing information or contradictory" in system
    assert "do not require a new architecture revision for every roadmap" in system
    assert "no existing component honestly covers a new slice" in system
    assert "leave its mapping empty as pending association" in system
    assert "4. Architect (only when in scope)" in system
    assert "use update_architecture before substantial new planning" not in system
    assert messages[-1] == {"role": "user", "content": user_request}
    tool = next(t["function"] for t in schemas if t["function"]["name"] == "update_architecture")
    assert (
        "Do not call when the user explicitly excludes or defers architecture work"
        in tool["description"]
    )
    assert "not a prerequisite for milestone planning" in tool["description"]
    assert "BEFORE drawing implementation milestones" not in tool["description"]
    reminder = captured[2][0][-1]["content"]
    assert "Honor explicit architecture exclusions or deferments" in reminder
    assert "do not block the requested roadmap" in reminder
    if not existing:
        assert '"architecture_missing"' in reminder
    after = app.db.get(project.id)
    assert after.milestone("M01").title == "Refined login roadmap"
    assert after.milestone("M01").architecture_components == (["api"] if existing else [])
    assert after.milestone("M01").architecture_revision == (2 if existing else 0)
    assert after.architectures == before.architectures
    assert after.diagrams == before.diagrams
    assert after.question is None
    assert events[-1]["summary"]["status"] == "completed"
    assert events[-1]["summary"]["changes"]["architecture"] is None


def test_explicit_architecture_request_keeps_tool_available_and_versioned(app):
    project = app.projects.create("Architecture requested")
    captured = []

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        if len(captured) == 1:
            yield chunk("update_architecture", architecture_spec().model_dump())
        else:
            yield {"type": "text", "text": "架构已更新"}

    app.settings.stream = stream

    async def run():
        return [event async for event in app.agent.stream(project.id, "请设计登录 API 的架构图")]

    events = asyncio.run(run())
    assert not [event for event in events if event["type"] in {"error", "tool_failed"}]
    assert "create or evolve architecture when requested or needed" in captured[0][0]["content"]
    after = app.db.get(project.id)
    assert len(after.architectures) == 1
    assert after.architectures[-1].diagram.nodes[0].id == "api"
    assert events[-1]["summary"]["changes"]["architecture"]["after_revision"] == 1


@pytest.mark.parametrize("existing", [False, True], ids=["no-architecture", "preserve-existing"])
def test_full_plan_prompt_and_apply_preserve_architecture_scope(app, existing):
    project = app.projects.create("Full login plan")
    plan = proposal()
    if existing:
        seed_architecture(app, project.id)
        plan.milestones[0].architecture_components = ["api"]
    apply_proposal(app, project, plan)
    before = app.db.get(project.id)
    plan.milestones[0].title = "Refined login roadmap"
    payload = plan.model_dump()
    # Legacy omission is not permission to erase the retained node's existing mapping.
    payload["milestones"][0].pop("architecture_components")
    captured = []

    async def complete(messages):
        captured.extend(deepcopy(messages))
        return json.dumps(payload), 20

    app.settings.complete = complete
    request = "调整 PR 路线图，架构以后再做"
    asyncio.run(app.planning.chat(project.id, request, propose=True))
    assert ARCHITECTURE_INTENT in captured[0]["content"]
    assert "全量计划接口不修改架构" in captured[0]["content"]
    assert "保留现有架构与有效组件映射" in captured[0]["content"]
    state, _ = json.JSONDecoder().raw_decode(captured[1]["content"].split("\n", 1)[1])
    if existing:
        assert state["architecture"] == {
            "number": 2,
            "summary": "Keep the existing API boundary",
            "technologies": [{"area": "API", "choice": "Python"}],
            "components": [{"id": "api", "label": "API"}],
        }
        assert "Historical architecture" not in captured[1]["content"]
    else:
        assert state["architecture"] is None
    assert captured[-1] == {"role": "user", "content": request}
    pending = app.db.get(project.id)
    assert pending.architectures == before.architectures
    after = app.planning.apply(project.id, pending.revision)
    assert after.milestone("M01").title == "Refined login roadmap"
    assert after.milestone("M01").architecture_components == (["api"] if existing else [])
    assert after.architectures == before.architectures
    assert after.diagrams == before.diagrams
