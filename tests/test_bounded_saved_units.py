"""Frozen QA55 offline replay. Mock deltas/review never claim model repair quality."""
import asyncio
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from evograph.application.plan_batch_policy import (
    REPAIR_EXPERIMENT,
    SAVED_UNITS_EXPERIMENT,
    experiment_policy,
    request_stage_rejection,
)
from evograph.application.plan_budget import BudgetedSettings, BudgetExceededError
from evograph.application.plan_repair_context import REPAIR_INSTRUCTIONS, saved_units_repair_context
from evograph.application.plan_review import resolve_packet_pointer
from evograph.application.plan_units import _hash, _objects, all_units_complete
from evograph.domain.models import Project
from test_agent_stream import tool_chunks
from test_review_materiality import batch_for

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def stopped(app):
    provenance = json.loads((FIXTURES / "qa55-saved-units-provenance.json").read_text())
    snapshots = {}
    for name, expected in provenance["files"].items():
        raw = gzip.decompress((FIXTURES / expected["compressed_file"]).read_bytes())
        assert len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"]
        snapshots[name] = json.loads(raw)
    before = Project.model_validate(snapshots["project.json"])
    app.db.create(before)
    reviewed, previous = snapshots["candidates.json"]
    for record in (reviewed, previous):
        assert app.unified.store.save(record) == record
    return before, reviewed, previous


def argument(previous):
    return {"project_id": previous["project_id"], "version": SAVED_UNITS_EXPERIMENT,
            "predecessor_candidate_id": previous["id"], "schedule_hash": _hash(previous["work_units"])}


def collect(app, previous, *, selected=None, content=None):
    async def run():
        return [e async for e in app.unified.stream(previous["project_id"], content or previous["input"],
                experiment=selected or argument(previous))]
    return asyncio.run(run())


def offline_delta(record):
    """Synthetic text changes only: test checkpoint plumbing, never semantic fixes."""
    project = Project.model_validate(record["project"])
    objects = _objects(project)
    unit = next(u for u in record["work_units"]["units"] if u["state"] == "pending")
    result = {}
    for row in unit["changes"]:
        if row["kind"] == "contract":
            value = {key: deepcopy(objects["contract:" + row["id"]][key]) for key in row["fields"]}
            value["mechanism"] += " Offline fixture change; not a semantic repair judgment."
            result.setdefault("contracts", []).append({"key": row["id"], **value})
        else:
            for field in row["fields"]:
                result[field] = deepcopy(objects["architecture:architecture"][field])
                if field == "decisions":
                    result[field].append("Offline fixture metadata change; no implementation tested.")
                else:
                    result[field][0]["rationale"] += " Offline fixture only."
    return result


def offline_held_review(packet):
    issue = {"id": "offline-unassessed", "subjects": ["cross_contract_consistency"], "verdict": "unknown",
             "reason": "Offline fixture does not assess semantic repair quality",
             "counterexample": "Original review obligations remain unassessed in this fixture",
             "evidence_refs": ["/current_input"], "materiality": {
                 "obligation_ref": "/current_input", "obligation_excerpt": resolve_packet_pointer(packet, "/current_input"),
                 "affected_owner_ids": ["slice-2-customer-booking"], "gap_kind": "unresolved_semantics",
                 "boundary_refs": ["/candidate/plan_contract/bindings/0/mechanism"],
                 "necessary_plan_change": "Resolve original obligations in a real independent review"}}
    return batch_for(packet, issues=[issue]).model_dump()


def test_exact_frozen_three_units_resume_and_one_full_held_review(app, stopped):
    before, reviewed, previous = stopped
    seen = []
    agenda, admission = saved_units_repair_context(before, previous, app.unified.store)
    assert len(agenda["plugins"]) == 1 and len(agenda["plugins"][0]["issues"]) == 2
    assert agenda["plugins"][0]["issues"] == reviewed["report"]["semantic_batch"]["issues"]
    async def stream(messages, schemas):
        names = {s["function"]["name"] for s in schemas}
        seen.append((names, deepcopy(messages)))
        if "submit_plan_delta" in names:
            assert names == {"submit_plan_delta", "ask_user"}
            name, raw = "submit_plan_delta", offline_delta(app.unified.store.latest(before.id))
        else:
            assert names == {"submit_plan_review"}
            name, raw = "submit_plan_review", offline_held_review(json.loads(messages[-1]["content"]))
        async for event in tool_chunks(name, raw):
            yield event
    app.settings.stream = stream
    collect(app, previous)
    saved = app.unified.store.latest(before.id)
    assert len(seen) == 4, saved["report"]
    assert saved["metrics"]["provider_calls"] == 4
    assert saved["planning_experiment"] == {"project_id": before.id, **experiment_policy(SAVED_UNITS_EXPERIMENT)}
    assert saved["metrics"]["budget"] == previous["metrics"]["budget"]
    assert saved["metrics"]["planning_experiment"]["limits"]["max_calls"] == 4
    assert saved["saved_units_admission"] == admission and saved["repair_agenda"] == agenda
    assert saved["work_units"]["manifest"] == previous["work_units"]["manifest"]
    for current, original in zip(saved["work_units"]["units"], previous["work_units"]["units"]):
        assert {k: v for k, v in current.items() if k != "state"} == {k: v for k, v in original.items() if k != "state"}
    assert saved["work_units"]["completed_ids"] == previous["work_units"]["pending_ids"]
    assert all_units_complete(saved) and len(saved["work_units"]["checkpoints"]) == 3
    assert saved["generation_progress"]["checkpoint_count"] == 4
    assert saved["generation_pause_reason"] == "experiment_review_complete"
    assert saved["status"] == "needs_resolution" and saved["harness_run"]["decision"] == "hold"
    assert len(saved["reviews"]) == len(saved["batch_reviews"]) == 1
    for index, (_, messages) in enumerate(seen[:3]):
        marker = "\nCurrent state (data):\n" if index == 0 else "\nCurrent saved candidate and assigned work unit (data):\n"
        context = json.loads(messages[0]["content"].split(marker)[1])
        assert REPAIR_INSTRUCTIONS in messages[0]["content"]
        assert context["repair_agenda"] == {**agenda, "applicability": "historical_needs_recheck"}
        assert context["current_unit"]["id"] == previous["work_units"]["pending_ids"][index]
        assert not {"inherited_findings", "inherited_semantic_findings", "findings", "prior_work_units"} & context.keys()
        assert set(context["allowed_requirement_source_ids"]) == {reviewed["id"], previous["id"], saved["id"]}
    assert app.unified.store.get(previous["id"]) == previous
    assert app.unified.store.get(reviewed["id"]) == reviewed
    assert app.db.get(before.id) == before


@pytest.mark.parametrize("damage", ["default", "old_repair", "wrong_predecessor", "changed_manifest", "rehashed_manifest",
    "changed_partition", "plan_changed", "source_changed", "source_evidence", "review_snapshot", "review_certificate",
    "missing_review", "prior_schedule", "progress", "stop_reason", "router_retry", "input_changed"])
def test_admission_damage_never_dispatches_or_saves_candidate(app, stopped, damage):
    before, reviewed, previous = stopped
    selected = argument(previous)
    content = None
    if damage == "default":
        # Existing default path still requires an explicit experimental stage.
        selected = None
    elif damage == "old_repair":
        selected = {"project_id": before.id, "version": REPAIR_EXPERIMENT}
    elif damage == "wrong_predecessor":
        selected["predecessor_candidate_id"] = reviewed["id"]
    elif damage in {"changed_manifest", "rehashed_manifest"}:
        previous["work_units"]["manifest"][0]["intent"] += " changed"
        if damage == "rehashed_manifest":
            previous["work_units"]["manifest_hash"] = _hash(previous["work_units"]["manifest"])
            previous["work_units"]["units"][0]["changes"][0]["intent"] += " changed"
            previous["work_units"]["units"][0]["hash"] = _hash(previous["work_units"]["units"][0]["changes"])
            selected = argument(previous)  # Even recomputing caller pins cannot forge router evidence.
    elif damage == "changed_partition":
        previous["work_units"]["units"][0]["estimated_text_bytes"] += 1
        selected = argument(previous)
    elif damage == "plan_changed":
        previous["project"]["plan_contract"]["bindings"][0]["mechanism"] += " changed"
        from evograph.application.plan_units import _identity
        previous["work_units"]["base_fingerprint"] = previous["work_units"]["expected_fingerprint"] = _identity(Project.model_validate(previous["project"]))
        selected = argument(previous)
    elif damage in {"source_changed", "source_evidence"}:
        source = previous["project"]["plan_contract"]["sources"][0]
        if damage == "source_changed":
            source["text"] += " changed"
        else:
            source["evidence"].append({"unsupported": True})
    elif damage == "review_snapshot":
        reviewed["harness_snapshots"][0]["candidate_json"] += " "
    elif damage == "review_certificate":
        reviewed["batch_reviews"][-1]["raw_arguments"] += " "
    elif damage == "missing_review":
        previous["resumes_candidate_id"] = "missing"
    elif damage == "prior_schedule":
        previous["prior_work_units"]["manifest_hash"] = "changed"
    elif damage == "progress":
        previous["generation_progress"]["checkpoint_count"] += 1
    elif damage == "stop_reason":
        previous["generation_pause_reason"] = "provider_error"
    elif damage == "router_retry":
        previous["tool_attempts"] *= 2
    else:
        content = previous["input"] + " new direction"
    app.unified.store.save(reviewed)
    previous = app.unified.store.save(previous)
    dispatched = []
    async def forbidden(*args, **kwargs):
        dispatched.append(True)
        raise AssertionError("Admission must stop before a provider")
        yield
    app.settings.stream = forbidden
    if damage == "default":
        async def run():
            return [e async for e in app.unified.stream(before.id, previous["input"])]
        events = asyncio.run(run())
    else:
        events = collect(app, previous, selected=selected, content=content)
    assert any(e["type"] == "error" for e in events)
    assert dispatched == []
    assert app.unified.store.latest(before.id) == previous
    assert app.db.get(before.id) == before


@pytest.mark.parametrize("failure", ["provider", "output", "invalid_delta", "forbidden_router", "same_response_retry"])
def test_first_failure_has_no_retry_no_review_and_preserves_atomic_checkpoints(app, stopped, failure):
    before, reviewed, previous = stopped
    calls = []
    async def stream(messages, schemas):
        calls.append(deepcopy(messages))
        if failure == "provider":
            raise OSError("Offline provider failure")
        if failure == "output":
            yield {"type": "text", "text": "x" * 98305}
        elif failure == "forbidden_router":
            async for event in tool_chunks("schedule_plan_changes", {"changes": []}):
                yield event
        else:
            async for event in tool_chunks("submit_plan_delta", {"target": "wrong unit"}):
                yield event
            if failure == "same_response_retry":
                async for event in tool_chunks("submit_plan_delta", offline_delta(app.unified.store.latest(before.id))):
                    yield {**event, "index": 1}
    app.settings.stream = stream
    collect(app, previous)
    saved = app.unified.store.latest(before.id)
    assert len(calls) == 1 and saved["metrics"]["provider_calls"] == 1
    assert saved["work_units"]["checkpoints"] == [] and not saved.get("reviews")
    assert saved["generation_progress"]["checkpoint_count"] == 1
    assert saved["status"] != "applied"
    assert app.unified.store.get(previous["id"]) == previous
    assert app.db.get(before.id) == before


def test_dispatch_fixed_sequence_and_canonical_limits(app):
    selected = {"project_id": "offline", **experiment_policy(SAVED_UNITS_EXPERIMENT)}
    for index in range(5):
        for stage, name in [("generation", "submit_plan_delta"), ("router", "schedule_plan_changes"),
                            ("semantic_review", "submit_plan_review")]:
            metrics = {"planning_experiment": selected, "provider_calls": index}
            result = request_stage_rejection(metrics, [{"function": {"name": name}}],
                                             "semantic_review" if stage == "semantic_review" else "generation")
            assert (result is None) == (index < 4 and selected["request_sequence"][index] == stage)
    dispatched = []
    async def forbidden(*args, **kwargs):
        dispatched.append(True)
        yield
    for damage in ("failed", "retry", "inflated_policy", "fifth"):
        metrics = {"planning_experiment": deepcopy(selected), "provider_calls": 1, "tokens": 0}
        if damage == "failed":
            metrics["calls"] = [{"termination_reason": "provider_error", "error_type": "OSError"}]
        elif damage == "retry":
            metrics["experiment_stopped"] = "experiment_first_failure"
        elif damage == "inflated_policy":
            metrics["planning_experiment"]["limits"]["max_calls"] = 99
        else:
            metrics["provider_calls"] = 4
        async def run():
            wrapper = BudgetedSettings(SimpleNamespace(stream=forbidden, secrets=None), metrics)
            with pytest.raises(BudgetExceededError):
                async for _ in wrapper.stream([], [{"function": {"name": "submit_plan_delta"}}]):
                    pass
        asyncio.run(run())
    assert dispatched == []


@pytest.mark.parametrize("failed_call", [2, 3, 4])
@pytest.mark.parametrize("failure", ["provider", "invalid_output"])
def test_mid_stage_failure_retains_only_prior_atomic_units_and_never_retries(app, stopped, failed_call, failure):
    before, reviewed, previous = stopped
    calls = []
    async def stream(messages, schemas):
        calls.append(deepcopy(messages))
        if len(calls) == failed_call:
            if failure == "provider":
                raise TimeoutError("Offline request timeout")
            name = "submit_plan_review" if failed_call == 4 else "submit_plan_delta"
            raw = {"invalid": "Offline malformed output"}
        else:
            name, raw = "submit_plan_delta", offline_delta(app.unified.store.latest(before.id))
        async for event in tool_chunks(name, raw):
            yield event
    app.settings.stream = stream
    collect(app, previous)
    saved = app.unified.store.latest(before.id)
    assert len(calls) == saved["metrics"]["provider_calls"] == failed_call
    assert len(saved["work_units"]["checkpoints"]) == failed_call - 1
    assert saved["generation_progress"]["checkpoint_count"] == failed_call
    assert saved["work_units"]["completed_ids"] == previous["work_units"]["pending_ids"][:failed_call - 1]
    assert saved["work_units"]["pending_ids"] == previous["work_units"]["pending_ids"][failed_call - 1:]
    assert saved["work_units"]["manifest"] == previous["work_units"]["manifest"]
    assert saved["status"] != "applied" and not saved.get("batch_reviews")
    if failed_call == 4:
        assert saved["harness_run"]["decision"] == "hold"
        semantic = next(x for x in saved["harness_run"]["executions"] if x["plugin_id"] == "semantic_review")
        assert semantic["status"] in {"timeout", "invalid_output"}
    else:
        assert not saved.get("harness_run")
    assert app.unified.store.get(previous["id"]) == previous
    assert app.unified.store.get(reviewed["id"]) == reviewed
    assert app.db.get(before.id) == before


@pytest.mark.parametrize("bad_call", [1, 2])
@pytest.mark.parametrize("shape", ["malformed_first", "unknown_first", "valid_first_bad_second", "interleaved"])
def test_complete_response_admission_precedes_any_current_unit_write(app, stopped, bad_call, shape):
    before, reviewed, previous = stopped
    dispatched = []
    async def stream(messages, schemas):
        dispatched.append(True)
        raw = offline_delta(app.unified.store.latest(before.id))
        if len(dispatched) != bad_call:
            async for event in tool_chunks("submit_plan_delta", raw):
                yield event
            return
        valid = {"type": "tool_delta", "index": 1, "id": "valid", "name": "submit_plan_delta",
                 "arguments": json.dumps(raw, ensure_ascii=False)}
        malformed = {"type": "tool_delta", "index": 0, "id": "bad", "name": "submit_plan_delta",
                     "arguments": '{"bad":'}
        if shape == "malformed_first":
            yield malformed
            yield valid
        elif shape == "unknown_first":
            yield {**malformed, "name": "schedule_plan_changes", "arguments": '{"changes":[]}'}
            yield valid
        elif shape == "valid_first_bad_second":
            yield {**valid, "index": 0}
            yield {**malformed, "index": 1}
        else:
            encoded = valid["arguments"]
            cut = len(encoded) // 2
            yield {**valid, "index": 0, "arguments": encoded[:cut]}
            yield {**malformed, "index": 1}
            yield {**valid, "index": 0, "id": "", "name": "", "arguments": encoded[cut:]}
    app.settings.stream = stream
    events = collect(app, previous)
    saved = app.unified.store.latest(before.id)
    assert len(dispatched) == saved["metrics"]["provider_calls"] == bad_call
    assert len(saved["work_units"]["checkpoints"]) == bad_call - 1
    assert len(saved.get("tool_attempts", [])) == bad_call - 1
    assert saved["work_units"]["completed_ids"] == previous["work_units"]["pending_ids"][:bad_call - 1]
    assert saved["status"] == "failed" and not saved.get("harness_run")
    assert any("saved-units response rejected" in e.get("message", "") for e in events if e["type"] == "error")
    assert any("saved-units response rejected" in f["message"] for f in saved["report"]["findings"])
    assert len(saved["metrics"]["calls"][-1]["response_audit"]["tool_calls"]) == 2
    assert app.unified.store.get(previous["id"]) == previous
    assert app.unified.store.get(reviewed["id"]) == reviewed
    assert app.db.get(before.id) == before
