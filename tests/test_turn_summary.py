import asyncio
import json
import threading

import anyio
import pytest
from conftest import proposal
from evograph.application.turn_summary import build_turn_summary, read_turn_summary
from evograph.domain.models import (
    ArchitectureRevision,
    BehaviorRevision,
    Milestone,
    PendingQuestion,
    Project,
    TargetVersion,
    UmlDiagram,
)


def milestone_args(mid="M01", title="Login", scope="target", statement="Username login succeeds"):
    node = proposal(node=mid, key=f"{mid}.login", statement=statement).milestones[0].model_dump()
    node["title"] = title
    node["scope"] = [f"{mid}.py"]
    node["behaviors"][0]["acceptance_scope"] = scope
    return node


def provider(app, calls):
    rounds = iter(calls)

    async def stream(messages, schemas):
        call = next(rounds, None)
        if call is None:
            yield {"type": "text", "text": "Model prose is not a change summary"}
        else:
            name, args = call
            yield {
                "type": "tool_delta",
                "index": 0,
                "id": "call",
                "name": name,
                "arguments": json.dumps(args),
            }

    app.settings.stream = stream


def run(app, pid, calls, *, question_id=None):
    provider(app, calls)

    async def collect():
        return [event async for event in app.agent.stream(pid, "Review the plan", question_id)]

    return asyncio.run(collect())


def outcome(app, pid, events):
    done = events[-1]
    assert done["type"] == "done"
    turn_id = next(event["turn_id"] for event in events if event["type"] == "started")
    assert done["turn_id"] == turn_id
    persisted = next(
        event for event in app.db.events(pid) if event["kind"] == "agent_turn_finished"
    )
    assert json.loads(persisted["detail"]) == done["summary"]
    assert app.agent.turn_result(pid, turn_id) == {
        "turn_id": turn_id,
        "pending": False,
        "summary": done["summary"],
    }
    assert pid not in app.agent.active_turns
    assert not app.operation_lock(pid).locked()
    return done["summary"]


@pytest.mark.parametrize("calls", [[], [("read_project", {})]])
def test_noop_and_read_only_turns_persist_factual_completed_summary(app, planned, calls):
    summary = outcome(app, planned.id, run(app, planned.id, calls))
    assert summary["status"] == "completed"
    assert not summary["changed"]
    assert summary["before_revision"] == planned.revision
    assert summary["after_revision"] == app.db.get(planned.id).revision - 1
    assert summary["changes"] == {
        "milestones": {"added": [], "updated": [], "removed": []},
        "dependencies": {"added": [], "updated": [], "removed": []},
        "target": None,
        "architecture": None,
        "other": [],
    }
    assert "Model prose" not in json.dumps(summary)


def test_mutation_labels_and_transient_edits_do_not_create_net_changes(app, planned):
    original = proposal().milestones[0].model_dump()
    events = run(
        app,
        planned.id,
        [
            ("update_milestone", {**original, "title": "Temporary title"}),
            ("update_milestone", original),
            ("create_milestone", milestone_args("TEMP")),
            ("remove_milestone", {"milestone_id": "TEMP"}),
        ],
    )
    summary = outcome(app, planned.id, events)
    assert events[-1]["changed"]  # Preserve the older tool-mutation stream contract.
    assert not summary["changed"]
    assert summary["changes"]["milestones"] == {"added": [], "updated": [], "removed": []}
    assert summary["changes"]["target"] is None
    assert len(app.db.get(planned.id).behaviors) > len(planned.behaviors)


def test_added_updated_removed_milestones_are_named_from_initial_and_final_state(app, planned):
    from evograph.domain.models import ProposedMilestone

    app.graph.upsert(planned.id, ProposedMilestone.model_validate(milestone_args("OLD")), True)
    app.graph.finalize(planned.id)
    original = proposal().milestones[0].model_dump()
    summary = outcome(
        app,
        planned.id,
        run(
            app,
            planned.id,
            [
                ("update_milestone", {**original, "title": "Final title"}),
                ("remove_milestone", {"milestone_id": "OLD"}),
                ("create_milestone", milestone_args("NEW", title="New deliverable")),
            ],
        ),
    )
    assert summary["changes"]["milestones"] == {
        "added": [{"id": "NEW", "title": "New deliverable", "fields": []}],
        "updated": [{"id": "M01", "title": "Final title", "fields": ["title"]}],
        "removed": [{"id": "OLD", "title": "Login", "fields": []}],
    }


def test_snapshot_projection_ignores_position_metrics_history_and_question():
    before = Project(
        name="Plan", milestones=[Milestone(id="M", title="Milestone", intent="Intent")]
    )
    after = before.model_copy(deep=True)
    after.revision += 10
    after.updated_at = "later"
    after.metrics["model_tokens"] = 700
    after.milestones[0].position = {"x": 100, "y": 200}
    after.question = PendingQuestion(prompt="Which route?", category="decision", options=["A", "B"])
    after.behaviors.append(
        BehaviorRevision(behavior_key="retired", version=1, statement="Old", owner="M")
    )
    assert not build_turn_summary(before, after, "turn", "waiting")["changed"]


def test_dependency_add_remove_and_reason_type_only_updates_are_separate():
    before = Project(
        name="Plan",
        milestones=[
            Milestone(id="A", title="A", intent="A"),
            Milestone(
                id="B", title="B", intent="B", dependencies=["A"], dependency_reasons={"A": "Old"}
            ),
            Milestone(
                id="C",
                title="C",
                intent="C",
                dependencies=["A"],
                dependency_reasons={"A": "Remove"},
            ),
        ],
    )
    after = before.model_copy(deep=True)
    after.milestone("B").dependency_reasons["A"] = "New reason"
    after.milestone("B").dependency_types["A"] = "migration"
    after.milestone("C").dependencies = ["B"]
    after.milestone("C").dependency_reasons = {"B": "Add"}
    summary = build_turn_summary(before, after, "turn", "completed")
    assert summary["changes"]["milestones"]["updated"] == []
    assert summary["changes"]["dependencies"] == {
        "added": [{"source": "B", "target": "C", "reason": "Add", "type": "implementation"}],
        "updated": [
            {
                "source": "A",
                "target": "B",
                "fields": ["reason", "type"],
                "before": {"reason": "Old", "type": "implementation"},
                "after": {"reason": "New reason", "type": "migration"},
            }
        ],
        "removed": [{"source": "A", "target": "C", "reason": "Remove", "type": "implementation"}],
    }
    # Explicitly storing the legacy default type is not a semantic edge update.
    identical = before.model_copy(deep=True)
    identical.milestone("B").dependency_types["A"] = "implementation"
    assert not build_turn_summary(before, identical, "turn", "completed")["changed"]


@pytest.mark.parametrize("change", ["statement", "to_milestone", "to_target"])
def test_target_behavior_wording_and_scope_changes_use_actual_revision_ids(app, planned, change):
    node = proposal().milestones[0].model_dump()
    if change == "to_target":
        node["behaviors"][0]["acceptance_scope"] = "milestone"
        run(app, planned.id, [("update_milestone", node)])
    before = app.db.get(planned.id)
    if change == "statement":
        node["behaviors"][0]["statement"] = "Login reliably succeeds"
    else:
        node["behaviors"][0]["acceptance_scope"] = (
            "milestone" if change == "to_milestone" else "target"
        )
    summary = outcome(app, planned.id, run(app, planned.id, [("update_milestone", node)]))
    after = app.db.get(planned.id)
    target = summary["changes"]["target"]
    old_id, new_id = before.behaviors[-1].id, after.behaviors[-1].id
    assert target["before_version"] == before.targets[-1].number
    assert target["after_version"] == after.targets[-1].number
    assert not target["statement_changed"]
    assert target["required_behavior_ids"] == {
        "added": [] if change == "to_milestone" else [new_id],
        "removed": [] if change == "to_target" else [old_id],
    }
    assert target["required_behavior_changes"] == [
        {
            "behavior_key": "auth.login",
            "before_id": None if change == "to_target" else old_id,
            "after_id": None if change == "to_milestone" else new_id,
            "fields": ["statement" if change == "statement" else "acceptance_scope"],
        }
    ]
    assert after.milestone("M01").behavior_revision_ids == [new_id]
    assert after.behaviors[: len(before.behaviors)] == before.behaviors


def test_target_statement_and_architecture_use_stored_version_numbers():
    before = Project(
        name="Plan", targets=[TargetVersion(number=3, statement="Old", required_behavior_ids=[])]
    )
    after = before.model_copy(deep=True)
    after.targets.append(TargetVersion(number=4, statement="New", required_behavior_ids=[]))
    after.architectures.append(
        ArchitectureRevision(
            number=7,
            summary="Architecture",
            technologies=[{"area": "server", "choice": "Python", "rationale": "Existing stack"}],
            diagram={
                "id": "arch",
                "title": "Architecture",
                "nodes": [{"id": "api", "label": "API"}],
            },
        )
    )
    summary = build_turn_summary(before, after, "turn", "completed")
    assert summary["changes"]["target"] == {
        "before_version": 3,
        "after_version": 4,
        "statement_changed": True,
        "required_behavior_ids": {"added": [], "removed": []},
        "required_behavior_changes": [],
    }
    assert summary["changes"]["architecture"] == {"before_revision": None, "after_revision": 7}


def test_architecture_and_uml_version_only_churn_is_not_a_planning_change():
    architecture = ArchitectureRevision(
        number=2,
        summary="Architecture",
        technologies=[{"area": "server", "choice": "Python", "rationale": "Existing stack"}],
        diagram={"id": "arch", "title": "Architecture", "nodes": [{"id": "api", "label": "API"}]},
    )
    diagram = UmlDiagram(
        id="flow", title="Flow", kind="sequence", source="A -> B", scope="API", revision=1
    )
    before = Project(name="Plan", architectures=[architecture], uml_diagrams=[diagram])
    after = before.model_copy(deep=True)
    after.architectures.append(architecture.model_copy(update={"number": 9, "created_at": "later"}))
    after.uml_diagrams.append(diagram.model_copy(update={"revision": 2}))
    assert not build_turn_summary(before, after, "turn", "completed")["changed"]
    after.uml_diagrams[-1].source = "B -> A"
    assert build_turn_summary(before, after, "turn", "completed")["changes"]["other"] == [
        "uml_diagrams"
    ]


def test_redundant_dependency_is_summarized_after_finalization(app):
    from evograph.domain.models import ProposedMilestone

    project = app.projects.create("Dependencies")
    for mid, dependencies in [("A", []), ("B", ["A"]), ("C", ["B"])]:
        node = milestone_args(mid)
        node["dependencies"] = dependencies
        node["dependency_reasons"] = {
            dependency: "Required implementation" for dependency in dependencies
        }
        app.graph.upsert(project.id, ProposedMilestone.model_validate(node), True)
    app.graph.finalize(project.id)
    events = run(
        app,
        project.id,
        [("add_dependency", {"source": "A", "target": "C", "reason": "Redundant edge"})],
    )
    summary = outcome(app, project.id, events)
    assert not summary["changed"]
    assert summary["changes"]["dependencies"] == {"added": [], "updated": [], "removed": []}
    assert app.db.get(project.id).milestone("C").dependencies == ["B"]


def test_waiting_question_then_answer_have_distinct_persisted_turns(app, planned):
    events = run(
        app,
        planned.id,
        [
            (
                "ask_user",
                {
                    "prompt": "Which login route?",
                    "category": "decision",
                    "options": ["Email", "Username"],
                },
            )
        ],
    )
    first = outcome(app, planned.id, events)
    assert first["status"] == "waiting" and not first["changed"]
    question = app.db.get(planned.id).question
    second = outcome(app, planned.id, run(app, planned.id, [], question_id=question.id))
    assert second["status"] == "completed" and not second["changed"]
    assert first["turn_id"] != second["turn_id"]
    assert app.agent.turn_result(planned.id, first["turn_id"])["summary"] == first


@pytest.mark.parametrize("interruption", ["failed", "stopped"])
def test_interrupted_turn_persists_committed_changes_after_finalize(app, planned, interruption):
    node = proposal().milestones[0].model_dump()
    node["behaviors"][0]["acceptance_scope"] = "milestone"

    async def exercise():
        committed = asyncio.Event()
        events = []

        async def stream(messages, schemas):
            yield {
                "type": "tool_delta",
                "index": 0,
                "name": "update_milestone",
                "arguments": json.dumps(node),
            }
            if interruption == "failed":
                raise RuntimeError("Provider disconnected")
            committed.set()
            await asyncio.Event().wait()

        async def collect():
            async for event in app.agent.stream(planned.id, "Keep this a step check"):
                events.append(event)

        app.settings.stream = stream
        if interruption == "stopped":
            task = asyncio.create_task(collect())
            await asyncio.wait_for(committed.wait(), 5)
            turn_id = events[0]["turn_id"]
            assert app.agent.turn_result(planned.id, turn_id) == {
                "turn_id": turn_id,
                "pending": True,
                "summary": None,
            }
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not any(event["type"] == "done" for event in events)
            summary = app.agent.turn_result(planned.id, turn_id)["summary"]
        else:
            await collect()
            summary = outcome(app, planned.id, events)
        assert summary["status"] == interruption
        assert summary["changed"]
        assert summary["changes"]["target"]["required_behavior_ids"]["removed"] == [
            planned.behaviors[-1].id
        ]
        assert app.db.get(planned.id).targets[-1].required_behavior_ids == []
        assert not app.operation_lock(planned.id).locked()
        assert planned.id not in app.agent.active_turns

    asyncio.run(exercise())


def test_cancellation_retains_lock_until_tool_worker_and_summary_finish(app, planned, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = app.graph.upsert
    node = proposal().milestones[0].model_dump()
    node["title"] = "Committed after cancellation"

    def slow_upsert(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.graph, "upsert", slow_upsert)
    provider(app, [("update_milestone", node)])

    async def exercise():
        events = []

        async def collect():
            async for event in app.agent.stream(planned.id, "Change title"):
                events.append(event)

        task = asyncio.create_task(collect())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            turn_id = events[0]["turn_id"]
            task.cancel()
            await asyncio.sleep(0)
            assert app.operation_lock(planned.id).locked()
            assert app.agent.turn_result(planned.id, turn_id)["pending"]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        summary = app.agent.turn_result(planned.id, turn_id)["summary"]
        assert summary["status"] == "stopped" and summary["changed"]
        assert summary["changes"]["milestones"]["updated"] == [
            {
                "id": "M01",
                "title": "Committed after cancellation",
                "fields": ["title"],
            }
        ]
        assert not app.operation_lock(planned.id).locked()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancellation", ["anyio_scope", "repeated_task_cancel"])
@pytest.mark.parametrize("worker_failure", [False, True])
def test_repeated_cancellation_drains_committing_worker_before_unlock_or_receipt(
    app, planned, monkeypatch, cancellation, worker_failure
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = app.graph.upsert
    node = proposal().milestones[0].model_dump()
    node["behaviors"][0]["acceptance_scope"] = "milestone"
    rounds = 0

    def delayed_upsert(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5)
            result = original(*args, **kwargs)
            if worker_failure:
                raise ValueError("Handler failed after its successful commit")
            return result
        finally:
            finished.set()

    async def stream(messages, schemas):
        nonlocal rounds
        rounds += 1
        yield {
            "type": "tool_delta",
            "index": 0,
            "name": "update_milestone",
            "arguments": json.dumps(node),
        }

    monkeypatch.setattr(app.graph, "upsert", delayed_upsert)
    app.settings.stream = stream

    async def exercise():
        events = []
        cancelled = asyncio.Event()

        async def collect():
            try:
                async for event in app.agent.stream(planned.id, "Make this a step check"):
                    events.append(event)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        def assert_still_committing():
            assert not finished.is_set()
            assert not cancelled.is_set()
            assert app.operation_lock(planned.id).locked()
            turn_id = events[0]["turn_id"]
            assert app.agent.turn_result(planned.id, turn_id) == {
                "turn_id": turn_id,
                "pending": True,
                "summary": None,
            }
            assert not any(
                event["kind"] == "agent_turn_finished" for event in app.db.events(planned.id)
            )

        try:
            if cancellation == "anyio_scope":
                async with anyio.create_task_group() as group:
                    group.start_soon(collect)
                    assert await asyncio.to_thread(entered.wait, 5)
                    group.cancel_scope.cancel()
                    # Allow repeated scope cancellation checkpoints while this
                    # test task independently controls the blocked worker.
                    with anyio.CancelScope(shield=True):
                        await asyncio.sleep(0.02)
                        assert_still_committing()
                        release.set()
                assert cancelled.is_set()
            else:
                task = asyncio.create_task(collect())
                assert await asyncio.to_thread(entered.wait, 5)
                for attempt in range(3):
                    task.cancel("original stop" if attempt == 0 else "repeated stop")
                    await asyncio.sleep(0.01)
                    assert_still_committing()
                release.set()
                with pytest.raises(asyncio.CancelledError, match="original stop"):
                    await task
        finally:
            release.set()

        assert finished.is_set()
        assert rounds == 1  # Worker errors must never swallow cancellation and resume the provider.
        assert not any(event["type"] in {"done", "tool_failed"} for event in events)
        result = app.agent.turn_result(planned.id, events[0]["turn_id"])
        assert not result["pending"]
        summary = result["summary"]
        assert summary["status"] == "stopped" and summary["changed"]
        assert summary["changes"]["target"]["required_behavior_ids"]["removed"] == [
            planned.behaviors[-1].id
        ]
        assert app.db.get(planned.id).targets[-1].required_behavior_ids == []
        assert not app.operation_lock(planned.id).locked()

    asyncio.run(exercise())


def test_finalize_failure_still_records_failed_outcome_and_releases_lock(app, planned, monkeypatch):
    def fail(_):
        raise ValueError("Finalization failed")

    monkeypatch.setattr(app.graph, "finalize", fail)
    node = proposal().milestones[0].model_dump()
    node["title"] = "Already committed"
    events = run(app, planned.id, [("update_milestone", node)])
    summary = outcome(app, planned.id, events)
    assert summary["status"] == "failed" and summary["changed"]
    assert any(event["type"] == "error" for event in events)


def test_finalize_failure_reports_saved_uncommitted_target_draft(app, planned, monkeypatch):
    def fail(_):
        raise ValueError("Finalization failed")

    monkeypatch.setattr(app.graph, "finalize", fail)
    events = run(app, planned.id, [("set_target", {"statement": "A new target draft"})])
    summary = outcome(app, planned.id, events)
    assert summary["status"] == "failed" and summary["changed"]
    assert summary["changes"]["target"] is None
    assert summary["changes"]["other"] == ["target_draft"]
    assert app.db.get(planned.id).target_draft == "A new target draft"
    assert app.db.get(planned.id).targets == planned.targets


def test_successful_target_draft_commit_does_not_report_uncommitted_draft(app, planned):
    events = run(app, planned.id, [("set_target", {"statement": "A committed target"})])
    summary = outcome(app, planned.id, events)
    assert summary["status"] == "completed" and summary["changed"]
    assert summary["changes"]["target"]["statement_changed"]
    assert "target_draft" not in summary["changes"]["other"]
    assert app.db.get(planned.id).target_draft is None


def test_legacy_and_unrelated_outcomes_do_not_masquerade_as_current_turn(app, planned):
    app.db.save(app.db.get(planned.id), "agent_turn_finished", "graph_changed=True; tokens=40")
    assert app.agent.turn_result(planned.id, "old") == {
        "turn_id": "old",
        "pending": False,
        "summary": None,
    }
    assert read_turn_summary("[]", "turn") is None
    assert read_turn_summary('{"version":1,"turn_id":"turn","status":[]}', "turn") is None
    summary = outcome(app, planned.id, run(app, planned.id, []))
    assert read_turn_summary(json.dumps(summary), "unrelated") is None
    assert read_turn_summary(json.dumps({**summary, "version": 2}), summary["turn_id"]) is None
