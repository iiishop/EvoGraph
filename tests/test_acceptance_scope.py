import asyncio
import json

import pytest
from conftest import accept_external, apply_proposal, external_report, proposal
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.domain.design_review import review_design
from evograph.domain.models import (
    BehaviorRevision,
    PlanProposal,
    Project,
    ProposedBehavior,
    ProposedMilestone,
)
from evograph.domain.policies import acceptance, current_evidence, readiness


def node(mid="M1", scopes=("target", "milestone"), dependencies=()):
    return ProposedMilestone(
        id=mid,
        title="Deliver login",
        intent="Introduce the lasting login contract through a safe transition",
        scope=["auth.py"],
        dependencies=list(dependencies),
        dependency_reasons={
            dep: "Requires the predecessor's accepted contract" for dep in dependencies
        },
        behaviors=[
            {
                "key": f"{mid}.behavior{i}",
                "statement": f"Observable contract {i}",
                "acceptance_scope": scope,
            }
            for i, scope in enumerate(scopes)
        ],
    )


def prepare_execution(app, project_id, mid="M1"):
    app.execution.refresh(project_id)
    app.execution.resolve_obligation(project_id, mid, "scope", True, "Reviewed scope and contracts")


@pytest.fixture
def mixed(app, repository):
    p = app.projects.create("Mixed acceptance", repository=str(repository))
    app.graph.upsert(p.id, node(), create=True)
    app.graph.finalize(p.id)
    prepare_execution(app, p.id)
    return app.db.get(p.id)


def test_legacy_payloads_default_to_target_and_preserve_history(app, planned):
    app.execution.start(planned.id, "M01")
    accept_external(app, planned.id, "M01")
    p = app.db.get(planned.id)
    raw = p.model_dump()
    for behavior in raw["behaviors"]:
        behavior.pop("acceptance_scope")
    restored = Project.model_validate(raw)
    assert all(b.acceptance_scope == "target" for b in restored.behaviors)
    assert restored.targets == p.targets
    assert restored.evidence == p.evidence
    assert acceptance(restored)["achieved"]
    legacy_plan = proposal().model_dump()
    legacy_plan["milestones"][0]["behaviors"][0].pop("acceptance_scope")
    restored_plan = PlanProposal.model_validate(legacy_plan)
    assert restored_plan.milestones[0].behaviors[0].acceptance_scope == "target"


@pytest.mark.parametrize("model", [ProposedBehavior, BehaviorRevision])
def test_scope_is_a_bounded_enum_with_legacy_default(model):
    fields = {"key": "contract", "statement": "Observable result"}
    if model is BehaviorRevision:
        fields = {
            "behavior_key": "contract",
            "statement": "Observable result",
            "version": 1,
            "owner": "M1",
        }
    assert model(**fields).acceptance_scope == "target"
    with pytest.raises(ValueError, match="acceptance_scope"):
        model(**fields, acceptance_scope="optional")
    schema = model.model_json_schema()["properties"]["acceptance_scope"]
    assert schema["default"] == "target"
    assert schema["enum"] == ["target", "milestone"]


def test_mixed_scopes_keep_complete_milestone_and_idempotent_target(app, mixed):
    assert len(mixed.milestone("M1").behavior_revision_ids) == 2
    assert mixed.targets[-1].required_behavior_ids == [mixed.behaviors[0].id]
    before = mixed.model_dump()
    app.graph.finalize(mixed.id)
    assert app.db.get(mixed.id).model_dump() == before
    app.graph.upsert(mixed.id, node(), create=False)
    app.graph.finalize(mixed.id)
    p = app.db.get(mixed.id)
    assert p.behaviors == mixed.behaviors
    assert p.targets == mixed.targets


@pytest.mark.parametrize("index,new_scope", [(0, "milestone"), (1, "target")])
def test_scope_only_changes_create_revisions_and_preserve_proof_history(
    app, mixed, index, new_scope
):
    app.execution.start(mixed.id, "M1")
    accept_external(app, mixed.id, "M1")
    before = app.db.get(mixed.id)
    old = before.behaviors[index]
    changed = node()
    changed.behaviors[index].acceptance_scope = new_scope
    app.graph.upsert(mixed.id, changed, create=False)
    app.graph.finalize(mixed.id)
    after = app.db.get(mixed.id)
    new = after.behaviors[-1]
    assert new.behavior_key == old.behavior_key and new.statement == old.statement
    assert new.acceptance_scope == new_scope
    assert new.version == old.version + 1 and new.supersedes == old.id
    assert after.behaviors[: len(before.behaviors)] == before.behaviors
    assert after.targets[:-1] == before.targets
    assert after.evidence == before.evidence
    assert current_evidence(after, old.id) is None
    assert current_evidence(after, new.id) is None
    assert after.milestone("M1").status == "PLANNED"
    assert (new.id in after.targets[-1].required_behavior_ids) == (new_scope == "target")
    assert old.id not in after.targets[-1].required_behavior_ids


@pytest.mark.parametrize("change_statement", [False, True])
def test_omitted_incremental_scope_preserves_active_scope(app, mixed, change_statement):
    payload = node().model_dump()
    for behavior in payload["behaviors"]:
        behavior.pop("acceptance_scope")
    if change_statement:
        payload["behaviors"][1]["statement"] = "Updated transition contract"
    updated = ProposedMilestone.model_validate(payload)
    assert "acceptance_scope" not in updated.behaviors[1].model_fields_set
    app.graph.upsert(mixed.id, updated, create=False)
    app.graph.finalize(mixed.id)
    p = app.db.get(mixed.id)
    active_ids = p.milestone("M1").behavior_revision_ids
    active = [b for b in p.behaviors if b.id in active_ids]
    assert [b.acceptance_scope for b in active] == ["target", "milestone"]
    assert len(p.behaviors) == len(mixed.behaviors) + int(change_statement)
    assert p.targets == mixed.targets


def test_new_behavior_and_new_milestone_omissions_default_to_target(app, mixed):
    updated = node().model_dump()
    updated["behaviors"].append({"key": "new.result", "statement": "New lasting result"})
    app.graph.upsert(mixed.id, ProposedMilestone.model_validate(updated), create=False)
    created = node("M2").model_dump()
    for behavior in created["behaviors"]:
        behavior.pop("acceptance_scope")
    app.graph.upsert(mixed.id, ProposedMilestone.model_validate(created), create=True)
    app.graph.finalize(mixed.id)
    p = app.db.get(mixed.id)
    assert all(b.acceptance_scope == "target" for b in p.behaviors[2:])
    assert p.targets[-1].required_behavior_ids == [
        p.behaviors[0].id,
        *[b.id for b in p.behaviors[2:]],
    ]


def test_deleted_and_superseded_behaviors_leave_only_active_target_membership(app, mixed):
    app.execution.start(mixed.id, "M1")
    accept_external(app, mixed.id, "M1")
    before = app.db.get(mixed.id)
    updated = node()
    updated.behaviors[0].statement = "Replacement lasting contract"
    app.graph.upsert(mixed.id, updated, create=False)
    app.graph.finalize(mixed.id)
    revised = app.db.get(mixed.id)
    assert revised.targets[-1].required_behavior_ids == [revised.behaviors[-1].id]
    assert before.behaviors[0].id not in revised.targets[-1].required_behavior_ids
    app.graph.remove(mixed.id, "M1")
    app.graph.finalize(mixed.id)
    removed = app.db.get(mixed.id)
    assert removed.targets[-1].required_behavior_ids == []
    assert removed.behaviors == revised.behaviors
    assert removed.targets[:-1] == revised.targets
    assert removed.evidence == before.evidence
    assert acceptance(removed) == {"passed": 0, "total": 0, "achieved": False}


def test_milestone_only_graph_never_claims_empty_target_achieved(app, repository):
    p = app.projects.create("Transition only", repository=str(repository))
    app.graph.upsert(p.id, node(scopes=("milestone",)), create=True)
    app.graph.finalize(p.id)
    prepare_execution(app, p.id)
    app.execution.start(p.id, "M1")
    accept_external(app, p.id, "M1")
    p = app.db.get(p.id)
    assert p.milestone("M1").status == "VERIFIED_COMPLETE"
    assert p.targets[-1].required_behavior_ids == []
    assert acceptance(p) == {"passed": 0, "total": 0, "achieved": False}


def test_plan_apply_filters_retained_and_new_nodes_consistently(app, mixed):
    plan = PlanProposal(
        target=mixed.targets[-1].statement,
        summary="Keep and extend",
        milestones=[node(), node("M2")],
    )
    after = apply_proposal(app, mixed, plan)
    assert (
        after.milestone("M1").behavior_revision_ids == mixed.milestone("M1").behavior_revision_ids
    )
    assert len(after.milestone("M2").behavior_revision_ids) == 2
    assert after.targets[-1].required_behavior_ids == [after.behaviors[0].id, after.behaviors[2].id]
    target_history = list(after.targets)
    app.graph.finalize(after.id)
    assert app.db.get(after.id).targets == target_history


def test_full_plan_scope_change_requires_new_node_and_preserves_old_records(app, mixed):
    app.execution.start(mixed.id, "M1")
    accept_external(app, mixed.id, "M1")
    mixed = app.db.get(mixed.id)
    changed = node()
    changed.behaviors[1].acceptance_scope = "target"
    plan = PlanProposal(target="Final login", summary="Promote contract", milestones=[changed])
    with pytest.raises(ValueError, match="新的里程碑 ID"):
        apply_proposal(app, mixed, plan)
    changed.id = "M2"
    # Keep only the changed key: unchanged keys still retain their existing ownership.
    changed.behaviors = [changed.behaviors[1]]
    after = apply_proposal(app, mixed, plan)
    revised = after.behaviors[-1]
    assert revised.owner == "M2" and revised.acceptance_scope == "target"
    assert revised.supersedes == mixed.behaviors[1].id and revised.version == 2
    assert after.targets[-1].required_behavior_ids == [revised.id]
    assert after.targets[:-1] == mixed.targets
    assert after.behaviors[:-1] == mixed.behaviors
    assert after.evidence == mixed.evidence


def test_full_plan_can_demote_requirement_without_claiming_empty_target(app, mixed):
    changed = node()
    changed.id = "M2"
    changed.behaviors = [changed.behaviors[0]]
    changed.behaviors[0].acceptance_scope = "milestone"
    plan = PlanProposal(target="Transition only", summary="Reclassify step", milestones=[changed])
    after = apply_proposal(app, mixed, plan)
    assert after.behaviors[-1].supersedes == mixed.behaviors[0].id
    assert after.behaviors[-1].acceptance_scope == "milestone"
    assert after.targets[-1].required_behavior_ids == []
    assert acceptance(after) == {"passed": 0, "total": 0, "achieved": False}
    history = list(after.targets)
    app.graph.finalize(after.id)
    assert app.db.get(after.id).targets == history


def test_goal_membership_does_not_require_unrelated_milestone_only_work(app, repository):
    p = app.projects.create("Separate contracts", repository=str(repository))
    app.graph.upsert(p.id, node(scopes=("target",)), create=True)
    app.graph.upsert(p.id, node("M2", scopes=("milestone",)), create=True)
    app.graph.finalize(p.id)
    prepare_execution(app, p.id)
    app.execution.start(p.id, "M1")
    accept_external(app, p.id, "M1")
    p = app.db.get(p.id)
    assert acceptance(p) == {"passed": 1, "total": 1, "achieved": True}
    assert p.milestone("M2").status == "PLANNED"


def test_legacy_full_plan_cannot_silently_promote_retained_scoped_behavior(app, mixed):
    raw = node().model_dump()
    for behavior in raw["behaviors"]:
        behavior.pop("acceptance_scope")
    plan = PlanProposal(target="Final login", summary="Legacy replacement", milestones=[raw])
    with pytest.raises(ValueError, match="新的里程碑 ID"):
        apply_proposal(app, mixed, plan)
    assert app.db.get(mixed.id).behaviors == mixed.behaviors


def test_claimed_scope_cannot_change_and_all_checks_remain_required(app, mixed):
    app.execution.start(mixed.id, "M1")
    changed = node(scopes=("target", "target"))
    with pytest.raises(ValueError, match="释放"):
        app.graph.upsert(mixed.id, changed, create=False)
    implementation = app.acceptance.implementation(mixed.id, "M1")["prompt"]
    request = app.acceptance.prepare(mixed.id, "M1")
    for prompt in [implementation, request["prompt"]]:
        assert "目标要求" in prompt and "步骤验收，不计入最终目标" in prompt
        assert all(b.statement in prompt for b in mixed.behaviors)
    report = external_report(app, mixed.id, "M1", request["request_id"])
    report.checks.pop()
    with pytest.raises(ValueError, match="逐项覆盖全部行为"):
        app.acceptance.import_report(mixed.id, "M1", report)
    report = external_report(app, mixed.id, "M1", request["request_id"])
    report.checks[1].result = "FAIL"
    assert not app.acceptance.import_report(mixed.id, "M1", report)["released"]
    p = app.db.get(mixed.id)
    assert p.milestone("M1").lease_active
    assert not acceptance(p)["achieved"]  # Evidence remains all-or-nothing per milestone.
    assert accept_external(app, mixed.id, "M1")["released"]
    assert acceptance(app.db.get(mixed.id))["achieved"]


def test_milestone_only_prerequisite_still_requires_current_complete_acceptance(app, repository):
    p = app.projects.create("Transition dependency", repository=str(repository))
    app.graph.upsert(p.id, node(scopes=("milestone",)), create=True)
    app.graph.upsert(p.id, node("M2", scopes=("target",), dependencies=("M1",)), create=True)
    app.graph.finalize(p.id)
    prepare_execution(app, p.id)
    prepare_execution(app, p.id, "M2")
    assert not readiness(app.db.get(p.id), "M2")["safe_to_execute"]
    with pytest.raises(ValueError, match="M1"):
        app.execution.start(p.id, "M2")
    app.execution.start(p.id, "M1")
    accept_external(app, p.id, "M1")
    assert readiness(app.db.get(p.id), "M2")["safe_to_execute"]
    (repository / "auth.py").write_text("def login(): return 'updated'\n")
    refreshed = app.execution.refresh(p.id)
    assert not readiness(refreshed, "M2")["safe_to_execute"]
    assert any("M1" in blocker for blocker in readiness(refreshed, "M2")["blockers"])


def test_scope_guidance_is_exposed_in_agent_tool_contracts():
    registry = tools()
    proposed = ProposedBehavior.model_json_schema()["properties"]["acceptance_scope"]
    persisted = BehaviorRevision.model_json_schema()["properties"]["acceptance_scope"]
    assert proposed["description"].startswith(persisted["description"])
    for phrase in (
        "current user-approved goal is complete, including its exclusions",
        "local or transitional check outside that goal's final contract",
        "Deferred features stay excluded unless the user adds them",
        "Both scopes require milestone acceptance",
    ):
        assert phrase in proposed["description"]
    assert "preserve an existing behavior's scope" in proposed["description"]
    assert "new behaviors default to target" in proposed["description"]
    for name in ["create_milestone", "update_milestone"]:
        spec = registry[name]
        schema = spec.schema()["function"]["parameters"]
        assert schema["$defs"]["ProposedBehavior"]["properties"]["acceptance_scope"] == proposed
        assert "acceptance_scope" in spec.description
    plan_schema = PlanProposal.model_json_schema()
    assert plan_schema["$defs"]["ProposedBehavior"]["properties"]["acceptance_scope"] == proposed
    for name in ["create_milestone", "set_target"]:
        assert "current user-approved" in registry[name].description
        assert "Deferred features stay excluded unless the user adds them" in registry[name].description
    assert "Both remain mandatory milestone acceptance" in registry["create_milestone"].description
    assert "omitted acceptance_scope retains the active behavior's scope" in registry[
        "update_milestone"
    ].description
    assert "only from active target-scope" in registry["set_target"].description


@pytest.mark.parametrize("mode", ["agent", "full-plan"])
def test_current_goal_scope_guidance_reaches_provider(mode, app):
    """Assert actual prompt exposure, not the model's ability to follow the guidance."""
    project = app.projects.create("Current goal contract")
    captured = []

    async def stream(messages, schemas):
        captured.append(messages[0]["content"])
        yield {"type": "text", "text": "Fixture only; no generated plan quality claim."}

    async def complete(messages):
        captured.append(messages[0]["content"])
        return proposal().model_dump_json(), 1

    app.settings.stream = stream
    app.settings.complete = complete

    async def run():
        if mode == "agent":
            events = [event async for event in app.agent.stream(project.id, "Review current scope")]
            assert not any(event["type"] == "error" for event in events)
        else:
            await app.planning.chat(project.id, "Review current scope", propose=True)

    asyncio.run(run())
    assert len(captured) == 1
    system = " ".join(captured[0].split())
    if mode == "agent":
        assert "current user-approved goal, including its exclusions" in system
        assert "Deferred features stay excluded unless the user adds them" in system
        assert "Both scopes remain mandatory for that milestone's acceptance" in system
        assert "Preserve existing scopes on updates" in system
    else:
        assert "当前用户确认的目标完成时必须成立的要求（包括排除项）" in system
        assert "已推迟的功能不属于当前目标，除非用户将其加入范围" in system
        assert "两类行为都必须通过里程碑验收" in system
        assert "全量计划必须明确保留已有行为的 acceptance_scope" in system
        assert ProposedBehavior.model_json_schema()["properties"]["acceptance_scope"][
            "description"
        ] in system
    assert "lasting final-state" not in system
    assert "最终状态必须保持" not in system


@pytest.mark.parametrize("mode", ["incremental", "full-plan"])
def test_current_goal_exclusion_preserves_scope_and_membership(mode, app):
    """Explicit scope survives editing/review; no natural-language classification is asserted."""
    project = app.projects.create("Local records")
    target = "Deliver local records; shared accounts are deferred"
    current = node()
    current.behaviors[0].statement = (
        "In the current approved release, records remain local and no account-sharing action exists"
    )
    app.graph.upsert(project.id, current, create=True)
    app.graph.target(project.id, target)
    app.graph.finalize(project.id)
    before = app.db.get(project.id)
    current.title = "Refined local records delivery"
    if mode == "incremental":
        payload = current.model_dump()
        for behavior in payload["behaviors"]:
            behavior.pop("acceptance_scope")
        spec = tools()["update_milestone"]
        spec.handler(ToolContext(project.id, app), spec.parameters.model_validate(payload))
        app.graph.finalize(project.id)
    else:
        apply_proposal(app, before, PlanProposal(
            target=target, summary="Refine title only", milestones=[current],
        ))
    after = app.db.get(project.id)
    assert after.behaviors == before.behaviors
    assert after.targets == before.targets
    assert after.targets[-1].required_behavior_ids == [before.behaviors[0].id]
    snapshot = after.model_dump()
    report = review_design(after)
    membership = report["prospective_target_membership"]
    assert membership["target_behavior_count"] == 1
    assert membership["milestones"][0]["milestone_behavior_count"] == 1
    assert "当前用户确认目标" in membership["basis"]
    assert "已推迟的功能除非用户加入否则仍在范围外" in membership["basis"]
    assert "包括排除项" in report["semantic_review"][0]
    assert after.model_dump() == snapshot
    assert not acceptance(after)["achieved"]


@pytest.mark.parametrize("interruption", ["cancel", "failure"])
def test_interrupted_agent_finalizes_scope_edit_and_unlocks(app, mixed, interruption):
    changed = node(scopes=("milestone", "milestone"))

    async def run():
        committed = asyncio.Event()
        events = []

        async def stream(messages, schemas):
            yield {
                "type": "tool_delta",
                "index": 0,
                "id": "scope_edit",
                "name": "update_milestone",
                "arguments": json.dumps(changed.model_dump()),
            }
            if interruption == "failure":
                raise ValueError("Provider failed after the scope edit")
            committed.set()
            await asyncio.Event().wait()

        async def collect():
            async for event in app.agent.stream(mixed.id, "Make these step-only checks"):
                events.append(event)

        app.settings.stream = stream
        if interruption == "cancel":
            task = asyncio.create_task(collect())
            await asyncio.wait_for(committed.wait(), timeout=5)
            assert app.operation_lock(mixed.id).locked()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await collect()
            assert any(event["type"] == "error" for event in events)
            assert events[-1]["type"] == "done" and events[-1]["changed"]

        assert any(event["type"] == "graph_changed" for event in events)
        p = app.db.get(mixed.id)
        assert p.targets[:-1] == mixed.targets
        assert p.targets[-1].required_behavior_ids == []
        assert p.behaviors[-1].supersedes == mixed.behaviors[0].id
        assert p.behaviors[-1].acceptance_scope == "milestone"
        assert p.behaviors[: len(mixed.behaviors)] == mixed.behaviors
        assert not app.operation_lock(mixed.id).locked()

        async def finish(messages, schemas):
            yield {"type": "text", "text": "Current scope edit is preserved"}

        app.settings.stream = finish
        retry = [event async for event in app.agent.stream(mixed.id, "Check the current plan")]
        assert not any(event["type"] == "error" for event in retry)
        assert retry[-1]["type"] == "done" and not retry[-1]["changed"]
        assert app.db.get(mixed.id).targets == p.targets

    asyncio.run(run())
