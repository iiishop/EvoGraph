"""Independent offline regressions for a job source/candidate identity boundary.

Run with PYTHONPATH=<checkout>/backend:<checkout>/tests and its test interpreter.
Every project lives in pytest's temporary directory; provider streams are fake.
"""
import asyncio
import json
from contextlib import aclosing
from copy import deepcopy

import pytest
from evograph.application.api import Application
from evograph.application.plan_continuation_context import prior_pending_intent_context
from evograph.infrastructure.database import ConflictError
from test_bounded_planning_job import INPUT, collect, provider, request
from test_bounded_planning_job import seven as seven


class NoSecrets:
    def get(self, *args):
        raise AssertionError("This offline regression cannot read credentials")

    def set(self, *args):
        raise AssertionError("This offline regression cannot write credentials")


@pytest.fixture
def app(tmp_path):
    return Application(tmp_path / "isolated-test-data", NoSecrets())


@pytest.fixture
def phase_two_stopped(app, seven):
    calls = provider(app, seven, statement_suffix=" prior phase")
    collect(app, seven, request(app, seven, max_calls=6))
    old = app.unified.store.latest(seven.id)
    job = app.planning_jobs.store.get("offline-job")
    assert len(calls) == 6 and len(job["phases"]) == 2
    assert job["status"] == "stopped" and job["stop_reason"] == "aggregate_call_limit"
    assert old["id"] != old["planning_job"]["source_id"]
    assert old["work_units"]["pending_ids"] == ["unit-006", "unit-007"]
    assert app.db.get(seven.id) == seven
    return old


def capture_next_router(app, project, bounded):
    calls = provider(app, project, statement_suffix=" next user direction")
    options = {}
    if bounded:
        options["planning_job"] = {**request(app, project), "job_id": "next-fresh-job"}
    captured = []
    async def run():
        async with aclosing(app.agent.stream(project.id, INPUT + " Preserve the remaining acceptance changes.", **options)) as stream:
            async for event in stream:
                if event["type"] == "candidate_changed" and "第 1 次模型请求" in event["label"]:
                    captured.append(app.unified.store.latest(project.id))
                    if bounded:
                        app.planning_jobs.cancel(project.id, "next-fresh-job")
                    return
    asyncio.run(run())
    assert len(captured) == 1 and calls == []
    saved = app.unified.store.latest(project.id)
    system = next(m["content"] for m in captured[0]["model_inputs"][0]["messages"] if m["role"] == "system")
    context = json.loads(system.rsplit("\nCurrent state (data):\n", 1)[1])
    return saved, context


def exact_changes(projection):
    result = {}
    for unit in projection["units"]:
        if "changes" not in unit:
            continue
        result[unit["id"]] = deepcopy(unit["changes"])
        for change in result[unit["id"]]:
            if "intent_ref" in change:
                change["intent"] = projection["intent_texts"][change.pop("intent_ref")]
    return result


@pytest.mark.parametrize("bounded", [True, False], ids=["fresh-job", "ordinary-fresh-input"])
def test_phase_two_predecessor_pending_intents_reach_next_router(app, seven, phase_two_stopped, bounded):
    old = phase_two_stopped
    new, context = capture_next_router(app, seven, bounded)
    expected = {u["id"]: u["changes"] for u in old["work_units"]["units"]
                if u["id"] in old["work_units"]["pending_ids"] and u["state"] != "completed"}
    assert new["prior_work_units"] == old["work_units"]
    assert new["resumes_candidate_id"] == old["id"]
    assert exact_changes(context["prior_work_units"]) == expected
    assert exact_changes(prior_pending_intent_context(new, seven.id)) == expected
    assert app.unified.store.get(old["id"]) == old
    assert app.db.get(seven.id) == seven


def test_ordinary_turn_retains_five_call_budget_and_no_job(app, seven):
    calls = provider(app, seven)
    async def run():
        return [event async for event in app.agent.stream(seven.id, INPUT)]
    events = asyncio.run(run())
    record = app.unified.store.latest(seven.id)
    assert record["metrics"]["budget"]["max_calls"] == 5
    assert len(calls) == 4 and record["generation_pause_reason"] == "call_budget_reserved_for_review"
    assert "planning_job" not in record and app.planning_jobs.store.latest(seven.id) is None
    assert app.db.get(seven.id) == seven and not events[-1]["changed"]


@pytest.mark.parametrize("field", ["candidate_id", "record_hash", "source_id", "schedule_hash", "job_id", "missing-owner", "schedule-source", "resumes-candidate"])
def test_initial_save_rejects_forged_predecessor_ownership(app, seven, phase_two_stopped, field):
    new, _ = capture_next_router(app, seven, bounded=False)
    assert new.get("prior_work_units_owner")
    # Recreate only this temporary test candidate to exercise the initial save
    # path against the real saved predecessor, without mocking persistence.
    with app.db.connect() as connection:
        connection.execute("DELETE FROM plan_candidates WHERE id=?", (new["id"],))
    valid = app.unified.store.save(deepcopy(new))
    assert valid["prior_work_units_owner"] == new["prior_work_units_owner"]
    with app.db.connect() as connection:
        connection.execute("DELETE FROM plan_candidates WHERE id=?", (new["id"],))
    forged = deepcopy(new)
    if field == "missing-owner":
        forged.pop("prior_work_units_owner")
    elif field == "schedule-source":
        forged["prior_work_units"]["source_id"] = "foreign-source"
    elif field == "resumes-candidate":
        forged["resumes_candidate_id"] = "foreign-candidate"
    else:
        forged["prior_work_units_owner"][field] = "foreign-owner"
    with pytest.raises(ConflictError):
        app.unified.store.save(forged)
    assert app.unified.store.latest(seven.id)["id"] == phase_two_stopped["id"]


@pytest.mark.parametrize("field", ["source_id", "record_hash", "missing-owner", "schedule-source", "resumes-candidate"])
def test_existing_save_cannot_mutate_predecessor_ownership(app, seven, phase_two_stopped, field):
    new, _ = capture_next_router(app, seven, bounded=False)
    app.unified.store.save(deepcopy(new))
    forged = deepcopy(new)
    if field == "missing-owner":
        forged.pop("prior_work_units_owner")
    elif field == "schedule-source":
        forged["prior_work_units"]["source_id"] = "foreign-source"
    elif field == "resumes-candidate":
        forged["resumes_candidate_id"] = "foreign-candidate"
    else:
        forged["prior_work_units_owner"][field] = "foreign-owner"
    with pytest.raises(ConflictError):
        app.unified.store.save(forged)
    assert app.unified.store.latest(seven.id) == new


def test_current_job_completion_uses_stable_source_not_phase_candidate_id():
    from test_plan_continuation_context import complete_current, decoded, record

    current = record.__wrapped__()
    schedule = complete_current(current, ["unit-002"])
    current["planning_job"] = {"job_id": "current-job", "source_id": "stable-current-source"}
    schedule["source_id"] = current["planning_job"]["source_id"]
    before = deepcopy(current)
    projection = prior_pending_intent_context(current, current["project_id"])
    assert list(decoded(projection)) == ["unit-003", "unit-004"]
    assert projection["completed_in_current"]["unit_ids"] == ["unit-002"]
    assert projection["completed_in_current"]["source_id"] == "stable-current-source"
    assert current == before
    schedule["source_id"] = "foreign-current-source"
    assert "unit-002" in decoded(prior_pending_intent_context(current, current["project_id"]))


def test_ordinary_answer_to_closed_job_question_keeps_saved_predecessor_ownership(app, seven):
    from test_agent_stream import tool_chunks

    provider(app, seven)
    normal = app.settings.stream
    dispatched = 0
    async def ask_on_first_unit(messages, schemas, **kwargs):
        nonlocal dispatched
        dispatched += 1
        if dispatched == 2:
            async for event in tool_chunks("ask_user", {"prompt": "Keep local scope?",
                    "category": "decision", "options": ["Keep", "Drop"]}):
                yield event
        else:
            async for event in normal(messages, schemas, **kwargs):
                yield event
    app.settings.stream = ask_on_first_unit
    collect(app, seven, request(app, seven))
    old = app.unified.store.latest(seven.id)
    before_answer = app.db.get(seven.id)
    assert before_answer.question and old["work_units"]
    calls = provider(app, before_answer, statement_suffix=" answered")
    async def answer():
        return [event async for event in app.agent.stream(seven.id, "Keep",
                question_id=before_answer.question.id)]
    events = asyncio.run(answer())
    assert not [event for event in events if event["type"] == "error"]
    assert calls and app.unified.store.latest(seven.id)["id"] != old["id"]
    assert app.db.get(seven.id).question is None
    assert app.unified.store.latest(seven.id)["metrics"]["budget"]["max_calls"] == 5
