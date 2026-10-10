"""Offline protocol proofs only; fake review never establishes semantic quality."""
import asyncio
import json
from copy import deepcopy

import pytest
from evograph.application.plan_complete_change import (
    COMPLETE_CHANGE_LIMITS,
    COMPLETE_CHANGE_VERSION,
    complete,
    measure_delta,
)
from evograph.application.plan_harness import seal_snapshot
from evograph.application.plan_ir import PlanDelta, submit_plan_delta
from evograph.application.plan_stage import StagedDatabase
from evograph.domain.models import Project
from evograph.infrastructure.database import ConflictError
from evograph.infrastructure.plan_jobs import start_pins
from test_agent_stream import tool_chunks
from test_bounded_planning_job import collect
from test_bounded_planning_job import seven as seven
from test_review_materiality import batch_for


def request(app, project):
    return {"action": "start", "job_id": "complete-job", "mode": COMPLETE_CHANGE_VERSION,
            "limits": deepcopy(COMPLETE_CHANGE_LIMITS),
            "pins": start_pins(project, app.unified.store.latest(project.id))}


def delta():
    return {"summary": "Synthetic whole change", "contracts": [
        {"key": "check0", "mechanism": "Read-only Markdown traversal with explicit UTF-8 handling"}]}


def fake(app, project, *, output=None, failure=None, hook=None, hold=False):
    calls = []
    async def stream(messages, schemas, **kwargs):
        names = {s["function"]["name"] for s in schemas}
        record = app.unified.store.latest(project.id)
        calls.append({"names": names, "messages": deepcopy(messages), "record": deepcopy(record)})
        if hook:
            hook(len(calls), record)
        if failure == "timeout":
            raise TimeoutError("Synthetic provider timeout")
        if failure == "error":
            raise ValueError("Synthetic provider failure")
        if "submit_plan_delta" in names:
            assert names == {"submit_plan_delta", "ask_user"}
            raw = output if output is not None else delta()
            if failure == "multi":
                async for event in tool_chunks("submit_plan_delta", raw):
                    yield event
                async for event in tool_chunks("submit_plan_delta", raw):
                    yield {**event, "index": 1} if event["type"] == "tool_delta" else event
            elif failure == "truncated":
                yield {"type": "tool_delta", "index": 0, "name": "submit_plan_delta", "arguments": json.dumps(raw)[:10]}
            elif failure == "question":
                async for event in tool_chunks("ask_user", {"prompt": "Which synthetic option?", "category": "decision", "options": ["A", "B"]}):
                    yield event
            else:
                async for event in tool_chunks("submit_plan_delta", raw):
                    yield event
        else:
            assert names == {"submit_plan_review"}
            packet = json.loads(messages[-1]["content"])
            issues = []
            if hold == "semantic":
                issues = [{"id": "synthetic-gap", "subjects": ["slice_activation:" + packet["candidate"]["behaviors"][0]["owner"]],
                    "verdict": "unknown", "reason": "Synthetic unresolved file decoding failure boundary",
                    "counterexample": "An invalid UTF-8 file has no specified observable report",
                    "evidence_refs": ["/candidate/behaviors/0", "/candidate/plan_contract/bindings/0"],
                    "materiality": {"obligation_ref": "/candidate/behaviors/0/statement",
                        "obligation_excerpt": packet["candidate"]["behaviors"][0]["statement"],
                        "affected_owner_ids": [packet["candidate"]["behaviors"][0]["owner"]], "gap_kind": "unresolved_semantics",
                        "boundary_refs": ["/candidate/plan_contract/bindings/0/mechanism"],
                        "necessary_plan_change": "Specify invalid UTF-8 reporting before claiming the parser acceptance"}}]
            raw = batch_for(packet, issues=issues).model_dump()
            if "coverage" in schemas[0]["function"]["parameters"]["properties"] and "statuses" in raw:
                raw["coverage"] = {s: verdict for verdict, subjects in raw.pop("statuses").items() for s in subjects}
            if hold is True:
                raw["candidate_hash"] = "invalid certificate"
            async for event in tool_chunks("submit_plan_review", raw):
                yield event
    app.settings.stream = stream
    return calls


def test_complete_delta_one_checkpoint_two_calls_atomic_apply(app, seven):
    canonical = seven.model_dump()
    calls = fake(app, seven)
    req = request(app, seven)
    events = collect(app, seven, req)
    record = app.unified.store.latest(seven.id)
    assert events[-1]["changed"], [e for e in events if e["type"] in {"error", "tool_failed"}]
    assert len(calls) == 2
    assert [r["names"] for r in calls] == [{"submit_plan_delta", "ask_user"}, {"submit_plan_review"}]
    assert "work_units" not in record and complete(record)
    assert len(record["compilations"]) == record["generation_progress"]["checkpoint_count"] == 1
    assert len(app.planning_jobs.store.get(req["job_id"])["phases"]) == 1
    assert record["project"]["behaviors"][:7] == canonical["behaviors"]
    assert app.db.get(seven.id).revision == seven.revision + 1
    assert len(record["retained_acceptance"]["claims"]) == 7
    assert [c["purpose"] for c in record["metrics"]["calls"]] == ["generation", "semantic_review"]
    assert all(c["dispatched"] and c["normal_stream_end"] for c in record["metrics"]["calls"])
    collect(app, seven, req)
    assert len(calls) == 2
    changed = deepcopy(req)
    changed.pop("mode")
    collect(app, seven, changed)
    assert len(calls) == 2
    view = app.projects.get(seven.id)["planning_job"]
    assert view["mode"] == COMPLETE_CHANGE_VERSION and not view["can_continue"]


@pytest.mark.parametrize("failure", ["multi", "truncated", "timeout", "error"])
def test_first_invalid_or_uncertain_response_zero_checkpoints(app, seven, failure):
    calls = fake(app, seven, failure=failure)
    events = collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 1 and not record.get("compilations")
    assert app.db.get(seven.id) == seven and not events[-1]["changed"]
    assert not app.projects.get(seven.id)["planning_job"]["can_continue"]
    assert not record.get("harness_run")
    assert record["metrics"]["calls"][0]["dispatched"]


@pytest.mark.parametrize("raw", [{}, {"contracts": [{"key": "missing", "mechanism": "bad"}]},
    {"contracts": [{"key": "check0", "requires_behavior_keys": ["check1"]}]}])
def test_bad_delta_or_noop_retains_only_rejected_audit(app, seven, raw):
    calls = fake(app, seven, output=raw)
    events = collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 1 and not record.get("compilations")
    assert record["tool_attempts"][0]["status"] == "failed"
    assert record["tool_attempts"][0]["raw_arguments"] == json.dumps(raw)
    assert app.db.get(seven.id) == seven and not events[-1]["changed"]


def test_actual_noop_cannot_claim_substantive_repair(app, seven):
    calls = fake(app, seven, output={"contracts": [{"key": "check0", "mechanism": seven.plan_contract.bindings[0].mechanism}]})
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 1 and not record.get("compilations")
    assert "no substantive change" in record["tool_attempts"][0]["payload"]["error"]


def test_review_invalid_output_never_repair_or_apply(app, seven):
    calls = fake(app, seven, hold=True)
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 2 and len(record["compilations"]) == 1
    assert app.db.get(seven.id) == seven
    assert record["status"] == "needs_resolution"
    assert not app.projects.get(seven.id)["planning_job"]["can_continue"]


def test_cancel_during_response_prevents_atomic_checkpoint(app, seven):
    calls = fake(app, seven, hook=lambda n, r: app.planning_jobs.cancel(seven.id, "complete-job"))
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 1 and not record.get("compilations")
    assert app.db.get(seven.id) == seven


def test_policy_caps_count_submitted_rows_not_project_size():
    assert measure_delta({"slices": [{"id": f"M{i}", "title": "Same restatement"} for i in range(6)]})[1]["slices"] == 6
    with pytest.raises(ValueError, match="slices 7"):
        measure_delta({"slices": [{"id": f"M{i}", "title": "Same restatement"} for i in range(7)]})
    assert measure_delta({"contracts": [{"key": f"c{i}", "mechanism": "x"} for i in range(8)]})[1]["contracts"] == 8
    with pytest.raises(ValueError, match="contracts 9"):
        measure_delta({"contracts": [{"key": f"c{i}", "mechanism": "x"} for i in range(9)]})


def test_empty_cold_start_and_changed_job_limits_not_admitted(app):
    project = app.projects.create("Empty")
    project.unified_planning = True
    app.db.save(project, "enable")
    project = app.db.get(project.id)
    calls = fake(app, project)
    collect(app, project, request(app, project))
    assert not calls and app.unified.store.latest(project.id) is None
    req = request(app, project)
    req["job_id"] = "bad-limits"
    req["limits"]["max_calls"] = 3
    with pytest.raises(ValueError, match="at most two calls"):
        app.planning_jobs.store.create(project.id, "New request", req)


def test_mode_is_bound_into_harness_snapshot(app, seven):
    calls = fake(app, seven, hold=True)
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    project = Project.model_validate(record["project"])
    original = seal_snapshot(seven, project, record)
    changed = deepcopy(record)
    changed["complete_change_admission"]["policy"]["version"] = "changed"
    assert seal_snapshot(seven, project, changed).snapshot_id != original.snapshot_id
    with pytest.raises(ConflictError):
        app.unified.store.save(changed)
    assert len(calls) == 2


def test_question_has_terminal_receipt_and_ordinary_answer_route(app, seven):
    from test_bounded_planning_job import provider
    calls = fake(app, seven, failure="question")
    req = request(app, seven)
    events = collect(app, seven, req)
    record = app.unified.store.latest(seven.id)
    job = app.planning_jobs.store.get(req["job_id"])
    assert len(calls) == 1 and not record.get("compilations")
    assert not [e for e in events if e["type"] in {"error", "tool_failed"}]
    assert events[-1]["summary"]["status"] == "waiting"
    assert job["stop_reason"] == "question" and job["status"] == "stopped"
    before = app.db.get(seven.id)
    assert record["planning_job_question"]["question_id"] == before.question.id
    assert record["metrics"]["calls"][0]["normal_stream_end"]
    assert not app.projects.get(seven.id)["planning_job"]["can_continue"]
    collect(app, seven, req)
    assert len(calls) == 1
    ordinary_calls = provider(app, before, statement_suffix=" explicit answer")
    async def answer():
        return [e async for e in app.agent.stream(seven.id, "A", question_id=before.question.id)]
    answered = asyncio.run(answer())
    assert ordinary_calls and not [e for e in answered if e["type"] == "error"]
    assert app.db.get(seven.id).question is None
    assert app.unified.store.get(record["id"]) == record
    assert app.planning_jobs.store.get(job["id"]) == job
    assert "planning_job" not in app.unified.store.latest(seven.id)


def test_cancel_at_checkpoint_writer_gate_prevents_mutation(app, seven, monkeypatch):
    saved = StagedDatabase.save
    def cancel_then_save(db, project, kind, detail="", *, compiler_audit=None):
        if compiler_audit is not None:
            app.planning_jobs.cancel(seven.id, "complete-job")
        return saved(db, project, kind, detail, compiler_audit=compiler_audit)
    monkeypatch.setattr(StagedDatabase, "save", cancel_then_save)
    calls = fake(app, seven)
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 1 and not record.get("compilations")
    assert record["generation_progress"]["checkpoint_count"] == 0
    assert app.db.get(seven.id) == seven


def test_exact_replay_no_progress_changed_replay_rejected(app, seven):
    from types import SimpleNamespace

    from evograph.agent_tools.base import ToolContext
    fake(app, seven, hold=True)
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    db = StagedDatabase(app.db, app.unified.store, deepcopy(record), record["planning_job"]["source_id"])
    ctx = ToolContext(seven.id, SimpleNamespace(db=db))
    before = deepcopy(db.record)
    assert submit_plan_delta(ctx, PlanDelta.model_validate(delta()))["status"] == "NO_PROGRESS"
    assert db.record == before and app.unified.store.get(record["id"]) == record
    changed = delta()
    changed["contracts"][0]["mechanism"] += " changed replay"
    with pytest.raises(ValueError, match="changed replay"):
        submit_plan_delta(ctx, PlanDelta.model_validate(changed))
    assert db.record == before


def test_whole_delta_closes_dependencies_together_and_preserves_separate_milestones(app, seven):
    raw = {"slices": [{"id": "M1", "dependencies": ["M0"], "dependency_reasons": {"M0": "Use parser result"}}],
           "contracts": [{"key": "check1", "requires_behavior_keys": ["check0"]}]}
    calls = fake(app, seven, output=raw)
    collect(app, seven, request(app, seven))
    saved = app.db.get(seven.id)
    assert len(calls) == 2 and len(saved.milestones) == 7
    assert saved.milestone("M1").dependencies == ["M0"]
    assert len(app.unified.store.latest(seven.id)["compilations"]) == 1


def test_metadata_only_disposition_remains_rejected_proposal_not_repair(app, seven):
    def with_metadata(n, record):
        if n == 1:
            claim = record["retained_acceptance"]["claims"][0]
            payload["contracts"][0]["acceptance_changes"] = [{
                "baseline_id": claim["id"], "disposition": "retained", "witness_keys": ["check0"],
                "kind": "preserving_refactor", "reason": "Explicit synthetic unchanged mapping", "acceptance_at": "M0"}]
    payload = {"contracts": [{"key": "check0"}]}
    calls = fake(app, seven, output=payload, hook=with_metadata)
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 1 and not record.get("compilations")
    assert "acceptance_changes" in record["tool_attempts"][0]["raw_arguments"]
    assert app.db.get(seven.id) == seven


@pytest.mark.parametrize("mutation", ["mode", "policy", "source", "entry", "count"])
def test_predispatch_admission_tampering_rejected_under_writer_lock(app, seven, mutation):
    checked = []
    calls = fake(app, seven)
    async def run():
        async for event in app.agent.stream(seven.id, "Clarify parser handling", planning_job=request(app, seven)):
            if checked or event["type"] != "candidate_changed":
                continue
            record = app.unified.store.latest(seven.id)
            if not record:
                continue
            forged = deepcopy(record)
            if mutation == "mode":
                forged.pop("planning_experiment")
            elif mutation == "policy":
                forged["complete_change_admission"]["policy"]["bounds"]["contracts"] = 99
            elif mutation == "source":
                forged["project"]["plan_contract"]["sources"][-1]["text"] = "Changed intent"
            elif mutation == "entry":
                forged["complete_change_admission"]["base_revision"] += 1
            else:
                forged["generation_progress"]["checkpoint_count"] += 1
                forged["complete_change_admission"]["starting_checkpoints"] += 1
            with pytest.raises(ConflictError):
                app.unified.store.save(forged)
            assert app.unified.store.get(record["id"]) == record
            checked.append(True)
    asyncio.run(run())
    assert checked == [True] and len(calls) == 2


def test_stale_canonical_at_checkpoint_writer_gate_prevents_mutation(app, seven, monkeypatch):
    saved = StagedDatabase.save
    def stale_then_save(db, project, kind, detail="", *, compiler_audit=None):
        if compiler_audit is not None:
            canonical = app.db.get(seven.id)
            canonical.description = "Independent canonical edit"
            app.db.save(canonical, "independent edit")
        return saved(db, project, kind, detail, compiler_audit=compiler_audit)
    monkeypatch.setattr(StagedDatabase, "save", stale_then_save)
    calls = fake(app, seven)
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 1 and not record.get("compilations")
    assert app.db.get(seven.id).description == "Independent canonical edit"
    assert app.db.get(seven.id).milestones == seven.milestones


def test_genuine_semantic_hold_is_terminal_with_no_automatic_repair(app, seven):
    calls = fake(app, seven, hold="semantic")
    collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert len(calls) == 2 and len(record["compilations"]) == 1
    run = record["harness_run"]
    assert run["decision"] == "hold"
    assert run["executions"][-1]["status"] == "completed", run["executions"][-1].get("detail")
    assert run["executions"][-1]["result"]["verdict"] == "unknown"
    assert app.db.get(seven.id) == seven
    assert app.planning_jobs.store.get("complete-job")["status"] == "stopped"


@pytest.mark.parametrize("field", ["declared_mechanism", "unmentioned_scope"])
def test_writer_replays_actual_delta_result_before_accepting_checkpoint(app, seven, monkeypatch, field):
    from evograph.application.plan_ir import compile_plan_delta
    from evograph.application.plan_patch import _completed_compiler_audit
    from evograph.application.plan_units import _identity

    save = app.unified.store.save
    altered = []
    def inconsistent_result(record, **kwargs):
        if not altered and record.get("compilations"):
            altered.append(field)
            durable = app.unified.store.get(record["id"])
            before = Project.model_validate(durable["project"])
            changed = deepcopy(record)
            result = Project.model_validate(changed["project"])
            if field == "declared_mechanism":
                result.plan_contract.bindings[0].mechanism = "Different mechanism from the declared delta"
            else:
                result.milestone("M6").scope = ["unmentioned-scope-change.py"]
            compiled = compile_plan_delta(before, changed["compilations"][0]["ir"], retained_record=durable)
            changed["compilations"] = [_completed_compiler_audit(compiled.audit, before, result, changed=True)]
            changed["project"] = result.model_dump()
            changed["generation_progress"]["planning_fingerprint"] = _identity(result)
            # A self-consistent audit/result digest is insufficient: the durable
            # writer must verify the actual private compiler/service output.
            return save(changed, **kwargs)
        return save(record, **kwargs)
    monkeypatch.setattr(app.unified.store, "save", inconsistent_result)
    calls = fake(app, seven)
    events = collect(app, seven, request(app, seven))
    record = app.unified.store.latest(seven.id)
    assert altered == [field] and len(calls) == 1
    assert not record.get("compilations") and app.db.get(seven.id) == seven
    assert any("exact whole compiler replay" in e.get("message", "")
               for e in events if e["type"] == "tool_failed")


def test_exact_compiler_replay_accepts_new_revisions_and_preserves_old_history(app, seven):
    raw = {"contracts": [{"key": "check0", "statement": "Report local broken links in section 0 with UTF-8 errors"}],
           "components": [{"id": "core", "description": "Parse and report local links, including invalid UTF-8"}],
           "architecture_summary": "Read-only local checker with explicit UTF-8 reporting"}
    before = seven.model_dump()
    calls = fake(app, seven, output=raw)
    events = collect(app, seven, request(app, seven))
    saved = app.db.get(seven.id)
    assert len(calls) == 2 and events[-1]["changed"]
    assert saved.behaviors[:len(seven.behaviors)] == seven.behaviors
    assert saved.targets[:len(seven.targets)] == seven.targets
    assert saved.architectures[:len(seven.architectures)] == seven.architectures
    assert saved.plans[:len(seven.plans)] == seven.plans
    assert seven.model_dump() == before


def test_pre_verifier_complete_change_admission_and_certificate_fail_closed(app, seven):
    from evograph.application.plan_complete_change import validate_admission

    fake(app, seven, hold=True)
    collect(app, seven, request(app, seven))
    current = app.unified.store.latest(seven.id)
    candidate = Project.model_validate(current["project"])
    old = deepcopy(current)
    assert old["complete_change_admission"]["policy"].pop("result_verifier") == "whole-patch-replay/v1"
    with pytest.raises(ValueError, match="policy, mode, source or admission changed"):
        validate_admission(old, candidate)
    assert not complete(old)
    assert seal_snapshot(seven, candidate, old).snapshot_id != seal_snapshot(seven, candidate, current).snapshot_id
    with pytest.raises(ConflictError):
        app.unified.store.save(old)
    assert app.unified.store.get(current["id"]) == current
