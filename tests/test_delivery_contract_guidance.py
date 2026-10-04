"""Guidance delivery and acceptance compatibility, not generated-design quality.

Paired examples are authored fixtures. No model infers whether either contract is
sufficient; supplied external results only exercise the existing acceptance gate.
"""

import asyncio
import json

import pytest
from conftest import external_report, proposal
from evograph.agent_tools import tools
from evograph.domain.design_review import SEMANTIC_REVIEW, review_design
from evograph.domain.models import PlanProposal, ProposedBehavior, ProposedMilestone
from evograph.domain.policies import acceptance, readiness

EXAMPLES = [
    pytest.param(
        "Direct-call contract with fixtures; no real operation is exposed",
        "Given representative input, a direct call returns the specified normalized value",
        "PASS",
        id="independently-demoable-foundation",
    ),
    pytest.param(
        "Operation is available now; its required protection arrives in a later slice",
        "Every available operation satisfies the protection supplied by the later slice",
        "FAIL",
        id="impossible-owned-acceptance",
    ),
]


def stage(scope, statement, acceptance_scope="target"):
    return ProposedMilestone(
        id="EARLY",
        title="Demonstrable first slice",
        intent="Deliver this slice's contract; full release comes later",
        scope=[scope],
        behaviors=[{
            "key": "early.contract",
            "statement": statement,
            "acceptance_scope": acceptance_scope,
        }],
    )


@pytest.mark.parametrize("scope,statement,external_result", EXAMPLES)
def test_paired_examples_receive_guidance_without_new_review_rounds(
    app, scope, statement, external_result,
):
    project = app.projects.create("Authored stage fixture")
    node = stage(scope, statement)
    captured = []

    async def stream(messages, schemas):
        captured.append([message.get("content") for message in messages])
        if len(captured) == 1:
            yield {
                "type": "tool_delta", "index": 0, "id": "fixture",
                "name": "create_milestone", "arguments": node.model_dump_json(),
            }
        else:
            yield {"type": "text", "text": "Authored fixture; no quality judgement."}

    app.settings.stream = stream

    async def run():
        return [event async for event in app.agent.stream(project.id, "检查本步交付契约")]

    events = asyncio.run(run())
    assert not [e for e in events if e["type"] in {"error", "tool_failed"}]
    assert len(captured) == 3  # Tool, attempted finish, existing one-shot review.
    system = " ".join(captured[0][0].split())
    for phrase in (
        "its owning slice plus its prerequisites, not the completed roadmap",
        "acceptance_scope selects final-goal membership; it never postpones",
        "bring that control forward or repartition",
        "preserving the user's final requirement",
        "boundary that prevents it and an observable check in the earlier slice",
        'deployment warning or "demo only" label alone is insufficient',
        "An isolated foundation may instead demonstrate its own concrete contract",
        "without promising future workflows or production readiness",
        "Do not add release infrastructure or gates where no real operation needs restricting",
        "Ground guarantees in the promised operating configuration",
        "Do not claim alternatives equivalent without checking the complete invariant argument",
    ):
        assert phrase in system
    reminder = captured[2][-1]
    assert "Compare each active behavior with its owning slice and prerequisites" in reminder
    assert "a later control or demo-only warning cannot discharge earlier acceptance" in reminder
    assert "Repair the scope/activation boundary, not just its risk wording" in reminder
    assert "基础切片能否仅验证自己的具体契约" in reminder
    assert len(SEMANTIC_REVIEW) == 14  # Replaces an existing question, not another checklist.
    saved = app.db.get(project.id)
    assert saved.milestone("EARLY").scope == [scope]
    assert saved.behaviors[0].statement == statement
    before = saved.model_dump()
    report = review_design(saved)
    assert saved.model_dump() == before
    # Both texts remain structurally legal; this is not a natural-language validator.
    assert {f["code"] for f in report["findings"]} == {"architecture_missing"}
    assert "不代表验收通过" in report["limitation"]
    assert saved.evidence == []


def test_shared_statement_guidance_keeps_schema_and_scope_semantics():
    behavior = ProposedBehavior.model_json_schema()
    description = behavior["properties"]["statement"]["description"]
    assert "this owning milestone and its prerequisites" in description
    assert "final-goal membership, not a later acceptance time" in description
    assert "give that foundation its own verifiable contract" in description
    assert set(behavior["properties"]) == {"key", "statement", "acceptance_scope"}
    assert set(behavior["required"]) == {"key", "statement"}
    assert behavior["properties"]["statement"]["minLength"] == 1
    assert behavior["properties"]["statement"]["maxLength"] == 1000
    scope = behavior["properties"]["acceptance_scope"]
    assert scope["enum"] == ["target", "milestone"] and scope["default"] == "target"
    assert "Both scopes require milestone acceptance" in scope["description"]
    schemas = [PlanProposal.model_json_schema()]
    schemas.extend(
        tools()[name].schema()["function"]["parameters"]
        for name in ("create_milestone", "update_milestone")
    )
    for schema in schemas:
        assert schema["$defs"]["ProposedBehavior"] == behavior


def test_legacy_full_plan_receives_statement_guidance_without_extra_calls(app):
    project = app.projects.create("Full-plan guidance fixture")
    captured = []

    async def complete(messages):
        captured.append(messages)
        return proposal().model_dump_json(), 1

    app.settings.complete = complete
    result = asyncio.run(app.planning.chat(project.id, "规划一个可演示基础切片", propose=True))
    assert result and len(captured) == 1
    schema_text = captured[0][0]["content"].split("JSON Schema：\n", 1)[1]
    delivered = json.loads(schema_text)["$defs"]["ProposedBehavior"]
    assert delivered == ProposedBehavior.model_json_schema()


@pytest.mark.parametrize("scope,statement,external_result", EXAMPLES)
@pytest.mark.parametrize("acceptance_scope", ["target", "milestone"])
def test_external_result_not_demo_label_controls_stage_readiness(
    app, repository, scope, statement, external_result, acceptance_scope,
):
    project = app.projects.create("Acceptance fixture", repository=str(repository))
    app.graph.upsert(project.id, stage(scope, statement, acceptance_scope), create=True)
    later = ProposedMilestone(
        id="LATER", title="Deliver the consumer", intent="Use the earlier contract",
        scope=["Consumer integration"], dependencies=["EARLY"],
        dependency_reasons={"EARLY": "Consumes the earlier callable contract"},
        behaviors=[{"key": "later.result", "statement": "Consumer produces its specified result"}],
    )
    app.graph.upsert(project.id, later, create=True)
    app.graph.finalize(project.id)
    app.execution.refresh(project.id)
    for mid in ("EARLY", "LATER"):
        app.execution.resolve_obligation(project.id, mid, "scope", True, "Fixture reviewed")
    saved = app.db.get(project.id)
    early_ids = saved.milestone("EARLY").behavior_revision_ids
    target_ids = saved.targets[-1].required_behavior_ids
    assert (early_ids[0] in target_ids) == (acceptance_scope == "target")
    assert not readiness(saved, "LATER")["safe_to_execute"]
    app.execution.start(project.id, "EARLY")
    request = app.acceptance.prepare(project.id, "EARLY")
    assert statement in request["prompt"]
    saved = app.db.get(project.id)
    assert saved.acceptance_requests[-1].behavior_revision_ids == early_ids
    report = external_report(app, project.id, "EARLY", request["request_id"], external_result)
    result = app.acceptance.import_report(project.id, "EARLY", report)
    passed = external_result == "PASS"
    assert result["released"] is passed
    saved = app.db.get(project.id)
    assert readiness(saved, "LATER")["safe_to_execute"] is passed
    assert saved.milestone("EARLY").status == ("VERIFIED_COMPLETE" if passed else "IN_PROGRESS")
    assert not acceptance(saved)["achieved"]  # The later target result remains required.
