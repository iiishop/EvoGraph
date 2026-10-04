"""Model-tool lifecycle intent, without inferring authorization from user prose."""

import asyncio
import json
from copy import deepcopy

import pytest
from conftest import apply_proposal
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.application.tool_execution import ToolExecutor
from evograph.domain.models import (
    BehaviorRevision,
    Evidence,
    Milestone,
    PlanProposal,
    Project,
    ProposedMilestone,
)
from evograph.infrastructure.database import ConflictError
from test_agent_stream import tool_chunks


def behavior(key, scope=None, statement=None):
    result = {"key": key, "statement": statement or f"Observable {key} result"}
    if scope is not None:
        result["acceptance_scope"] = scope
    return result


def node(*extra, mid="M1", **changes):
    return {
        "id": mid, "title": "Local document delivery", "intent": "Deliver current contracts",
        "scope": ["local.py"],
        "behaviors": [behavior("keep.target"), behavior("keep.step"), *extra],
        **changes,
    }


def invoke(app, pid, name, args):
    executor = ToolExecutor(ToolContext(pid, app), tools())
    return asyncio.run(executor.invoke(name, json.dumps(args)))


@pytest.fixture
def lifecycle(app):
    p = app.projects.create("Behavior lifecycle")
    initial = node(behavior("desktop.export"), behavior("retired.target"),
                   behavior("retired.step", "milestone"))
    initial["behaviors"][1]["acceptance_scope"] = "milestone"
    app.graph.upsert(p.id, ProposedMilestone.model_validate(initial), True)
    app.graph.finalize(p.id)
    p = app.db.get(p.id)
    p.evidence.append(Evidence(
        milestone_id="M1", behavior_revision_ids=list(p.milestones[0].behavior_revision_ids),
        baseline_id="historical-baseline", fingerprint="historical-fingerprint",
        command=[], result="PASS", output="Historical evidence", duration=0,
    ))
    app.db.save(p, "test_historical_evidence")
    app.graph.upsert(p.id, ProposedMilestone.model_validate(node(behavior("desktop.export"))), False)
    app.graph.finalize(p.id)
    return app.db.get(p.id)


def historical(p, key):
    return next(b for b in reversed(p.behaviors) if b.behavior_key == key)


@pytest.mark.parametrize("alter", [{}, {"statement": "Revised old requirement"},
                                   {"acceptance_scope": "milestone"}])
def test_unacknowledged_historical_key_rejects_entire_narrow_update(app, lifecycle, alter):
    args = node({**behavior("retired.target"), **alter}, title="Narrow desktop release")
    before = lifecycle.model_dump()
    events = app.db.events(lifecycle.id)
    result = invoke(app, lifecycle.id, "update_milestone", args)
    assert result["payload"]["ok"] is False
    assert "仅在历史中" in result["payload"]["error"]
    assert "不要为消除报错机械添加" in result["payload"]["error"]
    assert [event["type"] for event in result["events"]] == ["tool_failed"]
    assert app.db.get(lifecycle.id).model_dump() == before
    assert app.db.events(lifecycle.id) == events


def test_ordinary_remove_new_and_active_revision_keep_existing_omission_semantics(app, lifecycle):
    args = node(behavior("brand.new"))
    args["behaviors"][1]["statement"] = "Revised active step check"
    result = invoke(app, lifecycle.id, "update_milestone", args)
    assert result["payload"]["ok"]
    assert result["payload"]["result"]["restored_inactive_behaviors"] == []
    app.graph.finalize(lifecycle.id)
    saved = app.db.get(lifecycle.id)
    assert saved.behaviors[:len(lifecycle.behaviors)] == lifecycle.behaviors
    assert historical(saved, "keep.target").id == historical(lifecycle, "keep.target").id
    revised = historical(saved, "keep.step")
    assert revised.acceptance_scope == "milestone"
    assert revised.supersedes == historical(lifecycle, "keep.step").id
    assert historical(saved, "brand.new").acceptance_scope == "target"
    active_ids = saved.milestone("M1").behavior_revision_ids
    assert historical(saved, "desktop.export").id not in active_ids
    assert historical(saved, "retired.target").id not in active_ids
    assert saved.evidence == lifecycle.evidence
    assert saved.targets[:-1] == lifecycle.targets


@pytest.mark.parametrize("key", ["retired.target", "retired.step"])
@pytest.mark.parametrize("change_statement", [False, True])
@pytest.mark.parametrize("change_scope", [False, True])
def test_explicit_restoration_scope_revisions_history_receipt_and_repeat(
    app, lifecycle, key, change_statement, change_scope,
):
    old = historical(lifecycle, key)
    restored = behavior(key, statement="New restored contract" if change_statement else None)
    expected_scope = old.acceptance_scope
    if change_scope:
        expected_scope = "milestone" if old.acceptance_scope == "target" else "target"
        restored["acceptance_scope"] = expected_scope
    args = node(restored, restore_inactive_behavior_keys=[key])
    result = invoke(app, lifecycle.id, "update_milestone", args)
    assert result["payload"]["ok"]
    app.graph.finalize(lifecycle.id)
    saved = app.db.get(lifecycle.id)
    current = historical(saved, key)
    assert current.acceptance_scope == expected_scope
    assert current.id in saved.milestone("M1").behavior_revision_ids
    assert (current.id in saved.targets[-1].required_behavior_ids) == (expected_scope == "target")
    if change_statement or change_scope:
        assert current.supersedes == old.id and current.version == old.version + 1
    else:
        assert current.id == old.id
    receipt = [{
        "behavior_key": key, "previous_revision_id": old.id, "saved_revision_id": current.id,
        "previous_acceptance_scope": old.acceptance_scope,
        "saved_acceptance_scope": expected_scope, "state": "saved_active",
    }]
    assert result["payload"]["result"]["restored_inactive_behaviors"] == receipt
    assert result["events"][0]["restored_inactive_behaviors"] == receipt
    assert saved.behaviors[:len(lifecycle.behaviors)] == lifecycle.behaviors
    assert saved.targets[:len(lifecycle.targets)] == lifecycle.targets
    assert saved.evidence == lifecycle.evidence
    assert "restore_inactive_behavior_keys" not in saved.model_dump_json()
    repeated = invoke(app, lifecycle.id, "update_milestone", args)
    assert repeated["payload"]["ok"]
    assert repeated["payload"]["result"]["restored_inactive_behaviors"] == []
    app.graph.finalize(lifecycle.id)
    retried = app.db.get(lifecycle.id)
    assert retried.behaviors == saved.behaviors
    assert retried.targets == saved.targets
    assert retried.milestone("M1").behavior_revision_ids == saved.milestone("M1").behavior_revision_ids


@pytest.mark.parametrize("ack", [
    ["typo"], ["retired.target"], ["brand.new"], ["keep.target", "keep.target"],
    [""], ["x" * 101], [1], None,
])
def test_invalid_or_irrelevant_acknowledgments_are_atomic(app, lifecycle, ack):
    args = node(behavior("brand.new"), restore_inactive_behavior_keys=ack)
    before = app.db.get(lifecycle.id).model_dump()
    events = app.db.events(lifecycle.id)
    result = invoke(app, lifecycle.id, "update_milestone", args)
    assert result["payload"]["ok"] is False
    assert app.db.get(lifecycle.id).model_dump() == before
    assert app.db.events(lifecycle.id) == events


def test_create_requires_restoration_acknowledgment_and_preserves_owner_history(app, lifecycle):
    args = node(mid="M2", behaviors=[behavior("retired.step")])
    before = lifecycle.model_dump()
    assert not invoke(app, lifecycle.id, "create_milestone", args)["payload"]["ok"]
    assert app.db.get(lifecycle.id).model_dump() == before
    args["restore_inactive_behavior_keys"] = ["retired.step"]
    result = invoke(app, lifecycle.id, "create_milestone", args)
    assert result["payload"]["ok"]
    saved = app.db.get(lifecycle.id)
    restored = historical(saved, "retired.step")
    assert restored.owner == "M2" and restored.acceptance_scope == "milestone"
    assert restored.supersedes == historical(lifecycle, "retired.step").id
    assert saved.behaviors[:len(lifecycle.behaviors)] == lifecycle.behaviors


def test_acknowledgment_cannot_bypass_active_ownership(app, lifecycle):
    args = node(mid="M2", behaviors=[behavior("keep.target")],
                restore_inactive_behavior_keys=["keep.target"])
    before = lifecycle.model_dump()
    assert not invoke(app, lifecycle.id, "create_milestone", args)["payload"]["ok"]
    assert app.db.get(lifecycle.id).model_dump() == before


def test_key_is_active_even_when_a_later_revision_is_historical(app, lifecycle):
    newer = BehaviorRevision(
        behavior_key="keep.step", version=2, statement="Unused historical variant",
        acceptance_scope="target", owner="M1", supersedes=historical(lifecycle, "keep.step").id,
    )
    lifecycle.behaviors.append(newer)
    app.db.save(lifecycle, "test_inactive_later_revision")
    read = invoke(app, lifecycle.id, "read_project", {})["payload"]["result"]
    assert "keep.step" not in read["behavior_lifecycle"]["inactive_behavior_keys"]
    assert newer.id in read["behavior_lifecycle"]["historical_revision_ids"]
    result = invoke(app, lifecycle.id, "update_milestone", node())
    assert result["payload"]["ok"]
    assert result["payload"]["result"]["restored_inactive_behaviors"] == []
    assert historical(app.db.get(lifecycle.id), "keep.step").acceptance_scope == "milestone"


def test_source_node_references_do_not_activate_planned_behavior_keys(app, lifecycle):
    lifecycle.source_milestones.append(Milestone(
        id="SRC_observation", title="Source observation", intent="Observed source", origin="source",
        source_baseline_id="source-baseline",
        behavior_revision_ids=[historical(lifecycle, "retired.target").id],
    ))
    app.db.save(lifecycle, "test_source_reference")
    before = lifecycle.model_dump()
    result = invoke(app, lifecycle.id, "update_milestone", node(behavior("retired.target")))
    assert not result["payload"]["ok"]
    assert app.db.get(lifecycle.id).model_dump() == before


@pytest.mark.parametrize("failure", ["dependency", "conflict"])
def test_late_validation_and_commit_conflict_do_not_persist_restoration(
    app, lifecycle, monkeypatch, failure,
):
    args = node(behavior("retired.step", statement="Changed restored step"),
                restore_inactive_behavior_keys=["retired.step"])
    if failure == "dependency":
        args.update(dependencies=["unknown"], dependency_reasons={"unknown": "Invalid prerequisite"})
    else:
        def fail_save(*args, **kwargs):
            raise ConflictError("Simulated revision conflict before commit")
        monkeypatch.setattr(app.db, "save", fail_save)
    before = lifecycle.model_dump()
    events = app.db.events(lifecycle.id)
    result = invoke(app, lifecycle.id, "update_milestone", args)
    assert not result["payload"]["ok"]
    assert app.db.get(lifecycle.id).model_dump() == before
    assert app.db.events(lifecycle.id) == events


def test_legacy_project_direct_calls_and_full_plan_keep_prior_contract(app, lifecycle):
    assert Project.model_validate(lifecycle.model_dump()) == lifecycle
    args = node(behavior("retired.step"))
    # The old direct call still defaults an inactive omitted scope to target.
    app.graph.upsert(lifecycle.id, ProposedMilestone.model_validate(args), False)
    assert historical(app.db.get(lifecycle.id), "retired.step").acceptance_scope == "target"
    # The separate legacy full-plan route retains its existing revision rules.
    plan = PlanProposal(target="Legacy replacement", summary="Restore through a revised owner",
                        milestones=[node(mid="M3", behaviors=[behavior(
                            "retired.target", statement="Revised legacy restored requirement",
                        )])])
    saved = apply_proposal(app, app.db.get(lifecycle.id), plan)
    assert historical(saved, "retired.target").owner == "M3"
    assert saved.behaviors[:len(lifecycle.behaviors)] == lifecycle.behaviors
    assert saved.evidence == lifecycle.evidence
    assert "restore_inactive_behavior_keys" not in PlanProposal.model_json_schema()["$defs"][
        "ProposedMilestone"
    ]["properties"]


def test_initial_and_read_context_distinguish_current_membership_from_histories(app, lifecycle):
    before = lifecycle.model_dump()
    read = invoke(app, lifecycle.id, "read_project", {})["payload"]["result"]
    captured = []

    async def stream(messages, schemas):
        captured.append(deepcopy(messages))
        yield {"type": "text", "text": "只读取当前状态"}

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(lifecycle.id, "Inspect current scope")]

    asyncio.run(collect())
    initial = json.loads(captured[0][0]["content"].split("Current state (data):\n", 1)[1])
    assert initial["behavior_lifecycle"] == read["behavior_lifecycle"]
    metadata = read["behavior_lifecycle"]
    assert set(metadata["active_revision_ids"]) == set(lifecycle.milestone("M1").behavior_revision_ids)
    assert metadata["inactive_behavior_keys"] == ["retired.target", "retired.step"]
    assert metadata["latest_committed_target_version"] == lifecycle.targets[-1].number
    assert historical(lifecycle, "retired.target").id in metadata["historical_revision_ids"]
    assert historical(lifecycle, "keep.step").id in metadata["active_revision_ids"]
    assert read["behaviors"] == before["behaviors"]
    assert read["targets"] == before["targets"]
    assert "behavior_lifecycle" not in app.projects.get(lifecycle.id)
    assert "behavior_lifecycle" not in app.db.get(lifecycle.id).model_dump()


def test_legacy_planning_context_reuses_lifecycle_facts_without_proposal_flag(app, lifecycle):
    captured = []

    async def complete(messages, *args, **kwargs):
        captured.extend(deepcopy(messages))
        return "Read current contracts", 1

    app.settings.complete = complete
    asyncio.run(app.planning.chat(lifecycle.id, "Inspect current scope"))
    content = captured[1]["content"].split("当前项目状态（数据）：\n", 1)[1]
    state, _ = json.JSONDecoder().raw_decode(content)
    read = invoke(app, lifecycle.id, "read_project", {})["payload"]["result"]
    assert state["behavior_lifecycle"] == read["behavior_lifecycle"]
    assert state["behaviors"] == lifecycle.model_dump()["behaviors"]
    assert state["target"] == lifecycle.targets[-1].model_dump()
    assert "restore_inactive_behavior_keys" not in PlanProposal.model_json_schema()["$defs"][
        "ProposedMilestone"
    ]["properties"]


@pytest.mark.parametrize("acknowledge", [False, True])
def test_failure_after_tool_call_and_retry_preserve_correct_lifecycle(app, lifecycle, acknowledge):
    args = node(behavior("retired.step"))
    if acknowledge:
        args["restore_inactive_behavior_keys"] = ["retired.step"]

    async def stream(messages, schemas):
        async for chunk in tool_chunks("update_milestone", args):
            yield chunk
        raise ConnectionError("Simulated provider failure after the tool call")

    app.settings.stream = stream

    async def collect():
        return [event async for event in app.agent.stream(
            lifecycle.id, "Restore the prior local step check" if acknowledge
            else "Remove desktop export only and preserve everything else",
        )]

    events = asyncio.run(collect())
    saved = app.db.get(lifecycle.id)
    summary = events[-1]["summary"]
    assert summary["status"] == "failed"
    assert summary["changed"] is acknowledge
    assert saved.evidence == lifecycle.evidence
    if acknowledge:
        restored = historical(saved, "retired.step")
        assert restored.id in saved.milestone("M1").behavior_revision_ids
        assert restored.id not in saved.targets[-1].required_behavior_ids
        assert any(event["type"] == "graph_changed" for event in events)
        repeated = asyncio.run(collect())
        assert repeated[-1]["summary"]["changed"] is False
        receipt = next(event["restored_inactive_behaviors"] for event in repeated
                       if event["type"] == "graph_changed")
        assert receipt == []
        assert app.db.get(lifecycle.id).targets == saved.targets
        assert app.db.get(lifecycle.id).behaviors == saved.behaviors
    else:
        assert saved.milestones == lifecycle.milestones
        assert saved.targets == lifecycle.targets
        assert saved.behaviors == lifecycle.behaviors
        assert any(event["type"] == "tool_failed" for event in events)
    assert saved.question is None


def test_failure_keeps_prior_saved_edit_but_not_rejected_restoration(app, lifecycle):
    rounds = 0

    async def sequence(messages, schemas):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            async for chunk in tool_chunks("set_target", {"statement": "Current narrower release"}):
                yield chunk
        else:
            async for chunk in tool_chunks("update_milestone", node(behavior("retired.target"))):
                yield chunk
            raise ConnectionError("Provider failed after a rejected restoration")

    app.settings.stream = sequence

    async def collect():
        return [event async for event in app.agent.stream(lifecycle.id, "Narrow current release")]

    events = asyncio.run(collect())
    saved = app.db.get(lifecycle.id)
    assert events[-1]["summary"]["status"] == "failed"
    assert events[-1]["summary"]["changed"]
    assert saved.targets[-1].statement == "Current narrower release"
    assert saved.targets[-1].required_behavior_ids == lifecycle.targets[-1].required_behavior_ids
    assert saved.milestones == lifecycle.milestones
    assert saved.behaviors == lifecycle.behaviors
    assert historical(saved, "retired.target").id not in saved.targets[-1].required_behavior_ids
