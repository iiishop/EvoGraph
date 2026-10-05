"""Offline independent question terminal/ledger assertions; never a provider run."""
import asyncio
import json
from copy import deepcopy

import pytest
from evograph.application.api import Application
from evograph.application.plan_budget import BudgetedSettings
from evograph.application.plan_stage import StagedDatabase
from evograph.domain.plan_contracts import candidate_hash
from evograph.infrastructure.database import ConflictError
from evograph.infrastructure.plan_jobs import digest, guard_candidate_write
from test_agent_stream import tool_chunks
from test_bounded_planning_job import INPUT, provider, request
from test_bounded_planning_job import seven as seven


class NoSecrets:
    def get(self, *args):
        raise AssertionError("No credential reads in offline review")
    def set(self, *args):
        raise AssertionError("No credential writes in offline review")


@pytest.fixture
def app(tmp_path):
    return Application(tmp_path / "isolated-question-proof", NoSecrets())


def install_question_provider(app, project, on_question_dispatch=None):
    provider(app, project)
    ordinary = app.settings.stream
    dispatches = []
    async def fake(messages, schemas, **kwargs):
        dispatches.append({"tools": [s["function"]["name"] for s in schemas]})
        assert len(dispatches) <= 2, "A waiting bounded job must not dispatch a third call"
        if len(dispatches) == 2:
            if on_question_dispatch:
                on_question_dispatch()
            async for event in tool_chunks("ask_user", {"prompt": "Keep local scope?",
                    "category": "decision", "options": ["Keep", "Drop"]}):
                yield event
            yield {"type": "usage", "tokens": 7}
        else:
            async for event in ordinary(messages, schemas, **kwargs):
                yield event
    app.settings.stream = fake
    return dispatches


def run_question(app, project, *, on_question=None, on_event=None, on_question_dispatch=None):
    dispatches = install_question_provider(app, project, on_question_dispatch)
    req = request(app, project)
    async def run():
        events = []
        async for event in app.agent.stream(project.id, INPUT, planning_job=req):
            events.append(event)
            if on_event:
                on_event(event)
            if event["type"] == "question" and on_question:
                await on_question(event)
        return events
    events = asyncio.run(run())
    return req, dispatches, events


def assert_terminal(app, project, req, dispatches, events):
    assert len(dispatches) == 2
    assert not [event for event in events if event["type"] in {"error", "tool_failed"}]
    questions = [event for event in events if event["type"] == "question"]
    done = [event for event in events if event["type"] == "done"]
    assert len(questions) == len(done) == 1
    final = done[0]
    canonical = app.db.get(project.id)
    record = app.unified.store.latest(project.id)
    job = app.planning_jobs.store.get(req["job_id"])
    phase = job["phases"][0]
    view = app.projects.get(project.id)["planning_job"]
    assert canonical.revision == project.revision + 1 == record["base_revision"]
    assert candidate_hash(canonical) == candidate_hash(project)
    assert canonical.question.model_dump() == questions[0]["question"] == record["project"]["question"]
    assert record["status"] == "needs_resolution"
    assert record["metrics"]["provider_calls"] == record["metrics"]["dispatched_calls"] == 2
    calls = record["metrics"]["calls"]
    assert len(calls) == 2 and all(call["dispatched"] for call in calls)
    assert calls[0]["normal_stream_end"] and calls[0]["termination_reason"] == "stream_end"
    assert calls[1]["status"] == "interrupted" and not calls[1]["normal_stream_end"]
    assert calls[1]["termination_reason"] == "cancelled" and calls[1]["error_type"] == "GeneratorExit"
    assert [call["phase_call_id"] for call in calls] == [f"{phase['id']}:1", f"{phase['id']}:2"]
    assert record["metrics"]["input_bytes"] == sum(call["input_bytes"] for call in calls)
    assert record["metrics"]["tokens"] == view["spend"]["tokens"] == 7
    assert view["spend"]["calls"] == 2 and not view["spend"]["usage_complete"]
    assert calls[1]["usage_events"] == []  # Post-question tail usage was never observed
    assert view["spend"]["input_bytes"] == record["metrics"]["input_bytes"]
    assert record["metrics"]["budget"]["max_calls"] == 5
    assert not record.get("compilations") and not record.get("reviews") and not record.get("harness_run")
    assert len(job["phases"]) == 1 and job["status"] == "stopped" and job["stop_reason"] == "question"
    assert not view["can_continue"] and view["continue_pins"] is None
    assert phase["closed_at"] and not phase["safe_boundary"] and phase["next_kind"] is None
    assert phase["record_hash"] == digest(record)
    assert job["pins"] == req["pins"]
    assert final["changed"] is False and final["summary"]["status"] == "waiting"
    assert final["summary"]["before_revision"] == project.revision
    assert final["summary"]["after_revision"] == canonical.revision
    assert final["summary"]["candidate_outcome"]["id"] == record["id"]
    assert final["summary"]["candidate_outcome"]["canonical_unchanged"] is True
    with app.db.connect() as connection:
        rows = connection.execute("SELECT detail FROM events WHERE project_id=? AND kind='agent_turn_finished' "
            "AND json_extract(detail, '$.turn_id')=?", (project.id, phase["id"])).fetchall()
    assert len(rows) == 1 and json.loads(rows[0][0]) == final["summary"]
    assert phase["receipt_hash"] == digest(final["summary"])
    assert app.agent.turn_result(project.id, phase["id"])["summary"] == final["summary"]
    assert project.id not in app.agent.active_turns
    assert len([m for m in app.db.messages(project.id) if m["role"] == "user"]) == 1
    return record, job


def test_job_question_has_waiting_receipt_and_full_ledger(app, seven):
    req, dispatches, events = run_question(app, seven)
    assert_terminal(app, seven, req, dispatches, events)


def test_question_audit_window_forbids_third_dispatch_commit_and_mutations(app, seven):
    checked = []
    async def probe(event):
        record = app.unified.store.latest(seven.id)
        assert record.get("planning_job_question")
        before = deepcopy(record)
        canonical_before = app.db.get(seven.id)
        with pytest.raises(ConflictError):
            app.unified.store.pause_question(deepcopy(record), canonical_before.question)
        assert app.db.get(seven.id) == canonical_before
        assert app.unified.store.get(record["id"]) == before
        for mutation in ("remove-marker", "marker-phase", "question", "behavior", "schedule", "source", "status"):
            forged = deepcopy(record)
            if mutation == "remove-marker":
                forged.pop("planning_job_question")
            elif mutation == "marker-phase":
                forged["planning_job_question"]["phase_id"] = "foreign-phase"
            elif mutation == "question":
                forged["project"]["question"]["prompt"] = "Unauthorized replacement"
            elif mutation == "behavior":
                forged["project"]["behaviors"][0]["statement"] = "Unauthorized candidate rewrite"
            elif mutation == "schedule":
                forged["work_units"]["pending_ids"].pop()
            elif mutation == "source":
                forged["source_message_id"] = "foreign-message"
            else:
                forged["status"] = "applied"
            with pytest.raises(ConflictError):
                app.unified.store.save(forged)
            assert app.unified.store.get(record["id"]) == before
        with app.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            with pytest.raises(ConflictError):
                guard_candidate_write(app.db, connection, deepcopy(record), deepcopy(record), commit=True)
            connection.rollback()
        db = StagedDatabase(app.db, app.unified.store, deepcopy(record), record["planning_job"]["source_id"])
        db.metrics_ref = deepcopy(record["metrics"])
        budget = BudgetedSettings(app.settings, db.metrics_ref, db.checkpoint_metrics)
        with pytest.raises(ConflictError):
            async for _ in budget.stream([{"role": "user", "content": "Forbidden third call"}], []):
                pass
        assert app.unified.store.get(record["id"]) == before
        checked.append(True)
    req, dispatches, events = run_question(app, seven, on_question=probe)
    assert checked == [True]
    assert_terminal(app, seven, req, dispatches, events)


def test_question_marker_cannot_be_forged_before_store_transition(app, seven):
    checked = []
    def probe(event):
        if checked or event["type"] != "candidate_changed":
            return
        record = app.unified.store.latest(seven.id)
        if not record:
            return
        forged = deepcopy(record)
        forged["planning_job_question"] = {"phase_id": record["turn_id"], "question_id": "invented",
            "base_revision": seven.revision, "canonical_hash": candidate_hash(seven)}
        with pytest.raises(ConflictError):
            app.unified.store.save(forged)
        assert app.unified.store.get(record["id"]) == record
        checked.append(True)
    req, dispatches, events = run_question(app, seven, on_event=probe)
    assert checked == [True]
    assert_terminal(app, seven, req, dispatches, events)


def test_job_question_duplicate_start_and_answer_do_not_reuse_authorization(app, seven):
    req, dispatches, events = run_question(app, seven)
    record, job = assert_terminal(app, seven, req, dispatches, events)
    async def duplicate():
        return [event async for event in app.agent.stream(seven.id, INPUT, planning_job=req)]
    asyncio.run(duplicate())
    assert len(dispatches) == 2 and app.planning_jobs.store.get(job["id"]) == job
    before = app.db.get(seven.id)
    calls = provider(app, before, statement_suffix=" explicit answer")
    async def answer():
        return [event async for event in app.agent.stream(seven.id, "Keep", question_id=before.question.id)]
    answered = asyncio.run(answer())
    assert calls and not [event for event in answered if event["type"] == "error"]
    assert app.db.get(seven.id).question is None
    assert app.unified.store.get(record["id"]) == record
    assert app.planning_jobs.store.get(job["id"]) == job
    next_record = app.unified.store.latest(seven.id)
    assert next_record["id"] != record["id"] and "planning_job" not in next_record
    assert next_record["metrics"]["budget"]["max_calls"] == 5


def test_cancel_before_question_persistence_keeps_canonical_untouched(app, seven):
    def cancel():
        app.planning_jobs.cancel(seven.id, "offline-job")
    req, dispatches, events = run_question(app, seven, on_question_dispatch=cancel)
    assert len(dispatches) == 2
    assert not [event for event in events if event["type"] == "question"]
    assert app.db.get(seven.id) == seven
    view = app.projects.get(seven.id)["planning_job"]
    assert view["status"] == "cancelled" and view["spend"]["calls"] == 2
    assert not view["can_continue"]
