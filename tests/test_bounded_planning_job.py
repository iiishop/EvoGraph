"""Offline job protocol proofs. Fake acceptance does not establish model quality."""
import asyncio
import json
from copy import deepcopy

import pytest
from evograph.domain.models import Project
from evograph.infrastructure.database import ConflictError
from evograph.infrastructure.plan_jobs import PHASE_BUDGET, start_pins
from test_agent_stream import tool_chunks
from test_review_materiality import batch_for

INPUT = "Clarify all seven local checker acceptance statements, retaining their mechanisms and ownership."


@pytest.fixture
def seven(app):
    text = "Report all broken local Markdown links without modifying source files."
    p = Project.model_validate({"name": "Bounded offline job", "unified_planning": True,
        "targets": [{"number": 1, "statement": text, "required_behavior_ids": [f"B{i}" for i in range(7)]}],
        "milestones": [{"id": f"M{i}", "title": f"Checker {i}", "intent": text,
            "scope": [f"checker{i}.py"], "architecture_components": ["core"], "architecture_revision": 1,
            "behavior_revision_ids": [f"B{i}"]} for i in range(7)],
        "behaviors": [{"id": f"B{i}", "version": 1, "behavior_key": f"check{i}", "owner": f"M{i}",
            "statement": f"Report local broken links in section {i}"} for i in range(7)],
        "plan_contract": {"sources": [{"id": "original", "text": text}],
            "requirements": [{"id": "r", "source_id": "original", "quote": text}],
            "bindings": [{"behavior_key": f"check{i}", "behavior_revision_id": f"B{i}",
                "requirement_ids": ["r"], "component_ids": ["core"],
                "mechanism": "Read-only Markdown traversal. " + "检查只读路径边界。" * 160} for i in range(7)]},
        "architectures": [{"number": 1, "summary": "Local read-only checker",
            "technologies": [{"area": "runtime", "choice": "Python", "rationale": "Local execution"}],
            "diagram": {"id": "design", "title": "Checker", "nodes": [
                {"id": "core", "label": "Core", "description": "Parse and report local links"}],
                "milestone_ids": [f"M{i}" for i in range(7)]}}]})
    app.db.create(p)
    return p


def request(app, p, **limits):
    return {"action": "start", "job_id": "offline-job",
            "limits": {"max_phases": 3, "max_calls": 12, "max_input_bytes": 1179648, **limits},
            "pins": start_pins(p, app.unified.store.latest(p.id))}


def provider(app, p, hook=None, outcome="pass", statement_suffix=""):
    calls = []
    async def stream(messages, schemas, **kwargs):
        names = {s["function"]["name"] for s in schemas}
        record = app.unified.store.latest(p.id)
        calls.append({"names": names, "messages": deepcopy(messages), "record": deepcopy(record)})
        if hook:
            hook(len(calls), record)
        if outcome == "timeout":
            raise TimeoutError("Offline timeout")
        if "schedule_plan_changes" in names:
            name, raw = "schedule_plan_changes", {"changes": [{"kind": "contract", "id": f"check{i}",
                "fields": ["statement"], "uses": [], "intent": "Clarify exact existing acceptance"} for i in range(7)]}
        elif "submit_plan_delta" in names:
            pin = record["unit_request"]
            unit = next(u for u in record["work_units"]["units"] if u["id"] == pin["unit_id"])
            name, raw = "submit_plan_delta", {"contracts": [{"key": row["id"],
                "statement": "Report local broken Markdown links without writes in " + row["id"] + statement_suffix}
                for row in unit["changes"]]}
        else:
            assert names == {"submit_plan_review"}
            name = "submit_plan_review"
            raw = batch_for(json.loads(messages[-1]["content"])).model_dump()
            if "coverage" in schemas[0]["function"]["parameters"]["properties"] and "statuses" in raw:
                raw["coverage"] = {subject: verdict for verdict, subjects in raw.pop("statuses").items()
                                   for subject in subjects}
        async for event in tool_chunks(name, raw):
            yield event
        yield {"type": "usage", "tokens": 7}
    app.settings.stream = stream
    return calls


def collect(app, p, req, content=INPUT):
    async def run():
        return [e async for e in app.agent.stream(p.id, content, planning_job=req)]
    return asyncio.run(run())


def test_nine_calls_two_phases_stable_source_atomic_apply(app, seven):
    calls = provider(app, seven)
    req = request(app, seven)
    events = collect(app, seven, req)
    job = app.planning_jobs.store.get(req["job_id"])
    assert job["status"] == "applied", (job, [e for e in events if e["type"] in {"error", "tool_failed"}])
    assert len(calls) == 9
    assert len(job["phases"]) == 2
    records = [app.unified.store.get(p["candidate_id"]) for p in job["phases"]]
    assert [r["metrics"]["provider_calls"] for r in records] == [4, 5]
    assert all(r["metrics"]["budget"] == PHASE_BUDGET for r in records)
    assert len({c["phase_call_id"] for r in records for c in r["metrics"]["calls"]}) == 9
    assert all(r["project"]["plan_contract"]["sources"][-1]["id"] == job["source_id"] for r in records)
    assert len([m for m in app.db.messages(seven.id) if m["role"] == "user"]) == 1
    assert records[0]["source_message_id"] == records[1]["source_message_id"] == job["source_message_id"]
    assert records[1]["retained_acceptance"] == records[0]["retained_acceptance"]
    assert len(records[1]["retained_acceptance"]["claims"]) == 7
    assert all(row["origin"] == "canonical_accepted" for row in records[1]["retained_acceptance"]["claims"])
    assert records[1]["work_units"]["checkpoints"][:3] == records[0]["work_units"]["checkpoints"]
    assert len(records[1]["work_units"]["checkpoints"]) == 7
    assert records[-1]["harness_commit_replay"]["run"]["decision"] == "apply"
    assert app.db.get(seven.id).revision == seven.revision + 1
    assert app.db.get(seven.id).metrics["model_tokens"] == 63
    assert events[-1]["changed"]
    assert app.projects.get(seven.id)["planning_job"]["spend"]["calls"] == 9
    frozen = deepcopy(records[0])
    with pytest.raises(ConflictError):
        app.unified.store.save(records[0])
    assert app.unified.store.get(frozen["id"]) == frozen
    collect(app, seven, req)
    assert len(calls) == 9  # Duplicate start never creates another source/dispatch.


@pytest.mark.parametrize("limits,reason,expected_calls", [
    ({"max_calls": 6}, "aggregate_call_limit", 6),
    ({"max_input_bytes": 1}, "aggregate_input_limit", 0),
    ({"max_phases": 1, "max_calls": 5, "max_input_bytes": 393216}, "aggregate_phase_limit", 4),
])
def test_aggregate_exhaustion_never_resets_authorization(app, seven, limits, reason, expected_calls):
    calls = provider(app, seven)
    events = collect(app, seven, request(app, seven, **limits))
    view = app.projects.get(seven.id)["planning_job"]
    assert len(calls) == expected_calls
    assert view["status"] == "stopped" and view["stop_reason"] == reason
    assert view["authorization_needed"] and not view["can_continue"]
    assert view["spend"]["calls"] == expected_calls
    assert app.db.get(seven.id) == seven
    assert not events[-1]["changed"]


def test_timeout_is_charged_and_never_automatically_retried(app, seven):
    calls = provider(app, seven, outcome="timeout")
    collect(app, seven, request(app, seven))
    view = app.projects.get(seven.id)["planning_job"]
    assert len(calls) == view["spend"]["calls"] == 1
    assert view["stop_reason"] == "provider_timeout"
    assert not view["spend"]["usage_complete"] and not view["can_continue"]
    assert view["phase_number"] == 1


def test_cancel_at_final_predispatch_gate_is_persisted_and_charged(app, seven):
    calls = provider(app, seven)
    async def run():
        result = []
        async for event in app.agent.stream(seven.id, INPUT, planning_job=request(app, seven)):
            result.append(event)
            if event["type"] == "candidate_changed" and "第 1 次模型请求" in event["label"]:
                outcome = await app.dispatch("plan.job_cancel", {"project_id": seven.id, "job_id": "offline-job"})
                assert outcome["ok"]
        return result
    asyncio.run(run())
    view = app.projects.get(seven.id)["planning_job"]
    assert len(calls) == 0
    assert view["status"] == "cancelled" and view["spend"]["calls"] == 1
    assert not view["can_continue"]
    assert app.db.get(seven.id) == seven


def test_cancel_after_one_dispatched_call_stops_future_dispatch(app, seven):
    calls = provider(app, seven, hook=lambda n, r: app.planning_jobs.cancel(seven.id, "offline-job") if n == 1 else None)
    collect(app, seven, request(app, seven))
    assert len(calls) == 1
    view = app.projects.get(seven.id)["planning_job"]
    assert view["status"] == "cancelled" and view["spend"]["calls"] == 1
    assert app.db.get(seven.id) == seven


def test_stale_start_and_duplicate_changed_authorization_fail_before_dispatch(app, seven):
    calls = provider(app, seven)
    req = request(app, seven)
    req["pins"]["base_revision"] += 1
    collect(app, seven, req)
    assert not calls and app.planning_jobs.store.latest(seven.id) is None
    req = request(app, seven, max_calls=1)
    collect(app, seven, req)
    assert len(calls) == 1
    req["limits"]["max_calls"] = 12
    events = collect(app, seven, req)
    assert len(calls) == 1 and any(e["type"] == "error" for e in events)


def test_canonical_change_at_dispatch_is_refused(app, seven):
    calls = provider(app, seven)
    async def run():
        async for event in app.agent.stream(seven.id, INPUT, planning_job=request(app, seven)):
            if event["type"] == "candidate_changed" and "第 1 次模型请求" in event["label"]:
                project = app.db.get(seven.id)
                project.description = "A concurrent canonical change"
                app.db.save(project, "test_concurrent")
    asyncio.run(run())
    assert not calls
    assert app.planning_jobs.view(app.planning_jobs.store.get("offline-job"))["spend"]["calls"] == 1


def test_crash_with_reserved_or_uncertain_phase_never_restarts(app, seven):
    calls = provider(app, seven)
    req = request(app, seven)
    store = app.planning_jobs.store
    job, _ = store.create(seven.id, INPUT, req)
    job, phase = store.open_phase(job["id"], store.pins(job))
    # This is the durable state on crash before any stream/receipt is closed.
    collect(app, seven, req)
    assert not calls
    assert store.get(job["id"])["phases"] == job["phases"]
    bad = {"action": "continue", "job_id": job["id"], "pins": store.pins(job)}
    events = collect(app, seven, bad, content="")
    assert not calls and any(e["type"] == "error" for e in events)
    assert not store.get(job["id"])["phases"][-1]["closed_at"]


def test_ready_candidate_phase_byte_boundary_uses_genuine_recheck(app, seven):
    seven.plan_contract.sources[0].text += " Read-only retained context." * 1050
    seven = app.db.save(seven, "offline_size_fixture")
    calls = provider(app, seven)
    events = collect(app, seven, request(app, seven))
    job = app.planning_jobs.store.get("offline-job")
    assert job["status"] == "applied", (job, [e for e in events if e["type"] == "error"])
    assert len(calls) == 9
    assert [phase["kind"] for phase in job["phases"]] == ["generation", "generation", "review"]
    record = app.unified.store.latest(seven.id)
    assert record["metrics"]["provider_calls"] == 4
    assert record["metrics"]["admission_rejections"][-1]["reason"] == "total_input_limit"
    assert any(row["status"] == "budget_exhausted" for row in record["harness_run"]["executions"])
    assert len(record["review_attempts"]) == 1
    assert record["review_attempts"][0]["audit"]["metrics"]["provider_calls"] == 1
    assert app.projects.get(seven.id)["planning_job"]["phase_limits"]["max_calls"] == 1
    assert app.projects.get(seven.id)["planning_job"]["spend"]["calls"] == 9
    assert app.db.get(seven.id).metrics["model_tokens"] == 63
    assert len([m for m in app.db.messages(seven.id) if m["role"] == "user"]) == 1


def test_text_only_no_progress_is_not_applied(app, seven):
    async def text_only(*args, **kwargs):
        yield {"type": "text", "text": "No planning changes"}
        yield {"type": "usage", "tokens": 5}
    app.settings.stream = text_only
    events = collect(app, seven, request(app, seven))
    view = app.projects.get(seven.id)["planning_job"]
    assert view["status"] == "stopped" and view["stop_reason"] == "no_safe_progress"
    assert app.db.get(seven.id) == seven and not events[-1]["changed"]


def test_partial_usage_with_timeout_is_honestly_incomplete(app, seven):
    async def partial(*args, **kwargs):
        yield {"type": "usage", "tokens": 11, "usage_counter": "anthropic_input"}
        raise TimeoutError("Offline partial usage")
    app.settings.stream = partial
    collect(app, seven, request(app, seven))
    view = app.projects.get(seven.id)["planning_job"]
    assert view["spend"]["tokens"] == 11 and not view["spend"]["usage_complete"]
    assert view["stop_reason"] == "provider_timeout"


def test_cancel_after_atomic_apply_reports_already_applied(app, seven):
    provider(app, seven)
    async def run():
        async for event in app.agent.stream(seven.id, INPUT, planning_job=request(app, seven)):
            if event["type"] == "candidate_changed" and event["label"] == "规划已原子应用":
                view = app.planning_jobs.cancel(seven.id, "offline-job")
                assert view["status"] == "applied"
    asyncio.run(run())
    assert app.projects.get(seven.id)["planning_job"]["status"] == "applied"
    assert app.db.get(seven.id).revision == seven.revision + 1


def test_fresh_job_after_discard_uses_same_ui_and_server_pins(app, seven):
    provider(app, seven)
    collect(app, seven, request(app, seven, max_calls=1))
    old = app.unified.store.latest(seven.id)
    app.unified.store.discard(seven.id, old["id"])
    ui = app.projects.get(seven.id)
    assert ui["plan_candidate"] is None
    req = {**request(app, seven), "job_id": "after-discard", "pins": ui["planning_job_start_pins"]}
    calls = provider(app, seven)
    collect(app, seven, req)
    assert len(calls) == 9
    assert app.planning_jobs.store.get(req["job_id"])["status"] == "applied"


def test_retained_five_and_new_seven_after_fresh_manifest(app, seven):
    provider(app, seven, statement_suffix=" prior round")
    collect(app, seven, request(app, seven, max_calls=6))
    old = app.unified.store.latest(seven.id)
    assert old["generation_progress"]["checkpoint_count"] == 5
    old_copy = deepcopy(old)
    req = {**request(app, seven), "job_id": "new-feedback"}
    calls = provider(app, seven, statement_suffix=" new round")
    events = collect(app, seven, req)
    assert len(calls) == 9
    assert app.unified.store.get(old["id"]) == old_copy
    view = app.projects.get(seven.id)["planning_job"]
    assert view["status"] == "applied"
    assert view["progress"]["retained_checkpoints"] == 5
    assert view["progress"]["new_checkpoints"] == 7
    first = next(e["job"] for e in events if e["type"] == "planning_job_changed" and e["job"]["progress"]["new_checkpoints"] == 1)
    assert first["progress"]["retained_checkpoints"] == 5
    assert app.unified.store.latest(seven.id)["generation_progress"]["checkpoint_count"] == 12


def test_terminal_timeout_ledger_is_immutable_and_final_gate_refuses_retry(app, seven):
    from evograph.application.plan_budget import BudgetedSettings
    from evograph.application.plan_stage import StagedDatabase

    calls = provider(app, seven, outcome="timeout")
    checked = False
    async def run():
        nonlocal checked
        async for event in app.agent.stream(seven.id, INPUT, planning_job=request(app, seven)):
            if event["type"] != "error" or checked:
                continue
            record = app.unified.store.latest(seven.id)
            if not record or not record.get("metrics", {}).get("calls"):
                continue
            checked = True
            assert record["metrics"]["calls"][0]["termination_reason"] == "timeout"
            altered = deepcopy(record)
            altered["metrics"]["calls"][0]["elapsed_seconds"] += 1
            with pytest.raises(ConflictError):
                app.unified.store.save(altered)
            db = StagedDatabase(app.db, app.unified.store, record, record["planning_job"]["source_id"])
            db.metrics_ref = deepcopy(record["metrics"])
            budget = BudgetedSettings(app.settings, db.metrics_ref, db.checkpoint_metrics)
            with pytest.raises(ConflictError):
                async for _ in budget.stream([{"role": "user", "content": "forbidden retry"}], []):
                    pass
    asyncio.run(run())
    assert checked and len(calls) == 1


def test_stale_candidate_save_cannot_overwrite_phase_progress(app, seven):
    calls = provider(app, seven)
    checked = False
    async def run():
        nonlocal checked
        async for event in app.agent.stream(seven.id, INPUT, planning_job=request(app, seven)):
            if checked or event["type"] != "candidate_changed":
                continue
            first = app.unified.store.latest(seven.id)
            if not first:
                continue
            updated = app.unified.store.save(deepcopy(first))
            with pytest.raises(ConflictError):
                app.unified.store.save(first)
            # Cancel, since this deliberate competing writer also invalidates
            # the coordinator's stale in-memory write version.
            app.planning_jobs.cancel(seven.id, "offline-job")
            assert updated["job_write_version"] > first["job_write_version"]
            checked = True
    asyncio.run(run())
    assert checked and not calls


def test_new_job_after_applied_baseline_counts_its_own_checkpoints(app, seven):
    provider(app, seven, statement_suffix=" first")
    collect(app, seven, request(app, seven))
    before = app.db.get(seven.id)
    provider(app, before, statement_suffix=" next")
    req = {**request(app, before), "job_id": "new-canonical-job"}
    collect(app, before, req)
    view = app.projects.get(seven.id)["planning_job"]
    assert view["status"] == "applied"
    assert view["progress"]["retained_checkpoints"] == 0
    assert view["progress"]["new_checkpoints"] == 7


def test_closed_boundary_continue_is_idempotent_and_stale_pins_fail(app, seven):
    calls = provider(app, seven)
    store = app.planning_jobs.store
    job, _ = store.create(seven.id, INPUT, request(app, seven))
    job, phase = store.open_phase(job["id"], store.pins(job))
    context = {"job_id": job["id"], "source_id": job["source_id"], "phase_id": phase["id"], "phase_number": 1}
    async def first_phase():
        return [e async for e in app.unified.stream(seven.id, INPUT, _job_phase=context)]
    asyncio.run(first_phase())
    job = store.close_phase(job["id"], phase["id"])
    assert job["status"] == "paused" and len(calls) == 4
    resume = {"action": "continue", "job_id": job["id"], "pins": store.pins(job)}
    with app.db.connect() as c:
        row = c.execute("SELECT id, detail FROM events WHERE kind='agent_turn_finished' "
                        "AND json_extract(detail, '$.turn_id')=?", (phase["id"],)).fetchone()
        changed = json.loads(row[1])
        changed["candidate_outcome"]["canonical_unchanged"] = False
        c.execute("UPDATE events SET detail=? WHERE id=?", (json.dumps(changed), row[0]))
    assert any(e["type"] == "error" for e in collect(app, seven, resume, content=""))
    assert len(calls) == 4
    with app.db.connect() as c:
        c.execute("UPDATE events SET detail=? WHERE id=?", (row[1], row[0]))
    bad = deepcopy(resume)
    bad["pins"]["job_hash"] = "stale"
    assert any(e["type"] == "error" for e in collect(app, seven, bad, content=""))
    assert len(calls) == 4
    collect(app, seven, resume, content="")
    assert len(calls) == 9 and store.get(job["id"])["status"] == "applied"
    collect(app, seven, resume, content="")
    assert len(calls) == 9
    assert len([m for m in app.db.messages(seven.id) if m["role"] == "user"]) == 1


def test_held_manifest_stops_without_router_repair_or_next_phase(app, seven):
    calls = []
    async def held(*args, **kwargs):
        calls.append(1)
        async for event in tool_chunks("schedule_plan_changes", {"changes": [{"kind": "slice", "id": "uncovered",
                "fields": ["title", "intent", "scope"], "uses": [], "intent": "Missing owned acceptance"}]}):
            yield event
    app.settings.stream = held
    collect(app, seven, request(app, seven))
    view = app.projects.get(seven.id)["planning_job"]
    assert len(calls) == 1 and view["phase_number"] == 1
    assert view["stop_reason"] == "held_or_invalid_manifest"
    assert view["progress"]["held_units"] == 1 and not view["can_continue"]


def test_single_request_oversize_never_opens_another_phase(app, seven):
    seven.plan_contract.sources[0].text += " retained context" * 12000
    seven = app.db.save(seven, "oversize_fixture")
    calls = provider(app, seven)
    collect(app, seven, request(app, seven))
    view = app.projects.get(seven.id)["planning_job"]
    assert not calls and view["spend"]["calls"] == 0
    assert view["stop_reason"] == "request_input_limit" and view["phase_number"] == 1


def test_empty_transport_input_is_only_for_explicit_job_continue():
    from evograph.transport.http import AgentRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentRequest(project_id="p", content="")
    with pytest.raises(ValidationError):
        AgentRequest(project_id="p", content="", planning_job={"action": "start"})
    assert AgentRequest(project_id="p", content="", planning_job={"action": "continue"}).content == ""
