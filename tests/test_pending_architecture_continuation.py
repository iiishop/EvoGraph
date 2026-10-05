"""Pinned QA55 ordinary continuation. Synthetic deltas are not semantic evidence."""
import asyncio
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_batch_policy import (
    PENDING_ARCHITECTURE_EXPERIMENT,
    SAVED_UNITS_EXPERIMENT,
    experiment_policy,
    request_stage_rejection,
)
from evograph.application.plan_pending_continuation import admit_pending_continuation
from evograph.application.plan_units import (
    _hash,
    _objects,
    all_units_complete,
    resume_unit_schedule,
)
from evograph.domain.models import Project
from evograph.domain.plan_contracts import IntentSource, candidate_hash
from test_agent_stream import tool_chunks
from test_bounded_saved_units import offline_held_review

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def stopped_add(app):
    provenance = json.loads((FIXTURES / "qa55-pending-architecture-provenance.json").read_text())
    snapshots = {}
    for name, evidence in provenance["files"].items():
        raw = gzip.decompress((FIXTURES / evidence["compressed_file"]).read_bytes())
        assert len(raw) == evidence["bytes"] and hashlib.sha256(raw).hexdigest() == evidence["sha256"]
        snapshots[name] = json.loads(raw)
    before = Project.model_validate(snapshots["project.json"])
    previous = snapshots["candidates.json"][-1]
    assert previous["id"] == "572e49cdf1654727"
    app.db.create(before)
    assert app.unified.store.save(previous) == previous
    return before, previous


def argument(before, previous):
    return {"project_id": before.id, "version": PENDING_ARCHITECTURE_EXPERIMENT,
            "canonical_revision": before.revision, "canonical_hash": candidate_hash(before),
            "predecessor_candidate_id": previous["id"], "predecessor_record_hash": _hash(previous),
            "schedule_hash": _hash(previous["work_units"])}


def collect(app, before, previous, *, selected=None, content=None):
    async def run():
        return [e async for e in app.unified.stream(before.id, content or previous["input"],
                experiment=selected if selected is not None else argument(before, previous))]
    return asyncio.run(run())


def offline_delta(record):
    """Plumbing fixture preserving field identities, source quotes and graph references."""
    project = Project.model_validate(record["project"])
    objects = _objects(project)
    unit = next(u for u in record["work_units"]["units"] if u["state"] == "pending")
    result = {"summary": "Offline checkpoint plumbing; semantics remain unassessed"}
    quotes = {
        "req-reminder-email-day-before": "预约前一天发邮件提醒",
        "req-idempotent-notify-hold": "但不要因为重复执行就发两封邮件或重复占位",
        "req-waitlist-notify-on-cancel": "有人取消时通知候补顾客",
        "req-notify-failure-no-data-loss": "发送失败不能让预约数据丢掉",
        "req-single-shop-no-added-ops": "仍然是一家店，维护人手没增加",
    }
    for row in unit["changes"]:
        kind, identity = row["kind"], row["id"]
        def uses(field):
            return [u["id"] for u in row["uses"] if u["field"] == field]
        if kind == "component":
            old = objects.get("component:" + identity, {})
            value = {k: deepcopy(old[k]) for k in ("label", "description", "role") if k in old}
            value.setdefault("label", identity)
            value["description"] = "Offline fixture owned component, semantics unassessed."
            value.setdefault("role", "backend")
            result.setdefault("components", []).append({"id": identity, **value})
        elif kind == "requirement":
            result.setdefault("requirements", []).append({"id": identity, "kind": "outcome",
                "quote": quotes[identity], "source_id": record["resumes_candidate_id"]})
        elif kind == "slice":
            result.setdefault("slices", []).append({"id": identity, "title": identity,
                "intent": "Offline fixture delivery", "scope": ["Offline fixture scope"],
                "dependencies": uses("dependencies"),
                "dependency_reasons": {key: "Offline fixture prerequisite" for key in uses("dependencies")}})
        elif kind == "contract":
            if "contract:" + identity in objects:
                value = {"statement": objects["contract:" + identity]["statement"] + " Offline fixture."}
            else:
                value = {"owner": uses("owner")[0], "statement": "Offline fixture acceptance, unassessed",
                    "mechanism": "Offline fixture mechanism; no implementation or semantic proof",
                    "requirement_ids": uses("requirement_ids"), "component_ids": uses("component_ids"),
                    "requires_behavior_keys": uses("requires_behavior_keys")}
            result.setdefault("contracts", []).append({"key": identity, **value})
        elif kind == "relation":
            source, target, label = json.loads(identity)
            result.setdefault("relations", []).append({"source": source, "target": target, "label": label})
        elif kind == "architecture":
            for field in row["fields"]:
                architecture = objects["architecture:architecture"]
                if field == "architecture_summary":
                    result[field] = architecture["summary"] + " Offline fixture, not reviewed."
                elif field == "architecture_groups":
                    result[field] = deepcopy(architecture["diagram"].get("groups", []))
                elif field == "architecture_milestone_ids":
                    result[field] = [m.id for m in project.milestones]
                else:
                    result[field] = deepcopy(architecture[field])
    return result


def test_exact_four_remaining_units_and_default_resume_unchanged(stopped_add):
    before, previous = stopped_add
    frozen = deepcopy(previous)
    transformed, admission = admit_pending_continuation(argument(before, previous), before, previous, previous["input"])
    assert _hash(transformed) == "56078d4bab78ab0e6178fb14fb25f13404f0ac74b89c9e45ab792ce3ca1c2961"
    assert transformed["pending_ids"] == ["unit-002", "unit-004", "unit-006", "unit-007"]
    assert transformed["manifest"] == previous["work_units"]["manifest"]
    assert transformed["checkpoints"] == previous["work_units"]["checkpoints"]
    assert transformed["units"][0] == previous["work_units"]["units"][0]
    def rows(schedule):
        return sorted((r for u in schedule["units"] if u["state"] == "pending"
                       for r in u["changes"]), key=lambda r: r["change_id"])
    assert len(rows(transformed)) == 28 and rows(transformed) == rows(previous["work_units"])
    assert admission["pending_unit_mapping"]["unit-003"] == "unit-007"
    candidate = Project.model_validate(previous["project"])
    candidate.plan_contract.sources.append(IntentSource(id="ordinary", text=previous["input"],
        reference_context=previous["reference_context"]))
    resumed = resume_unit_schedule(previous, candidate, "ordinary", previous["input"])
    assert resumed["units"] == previous["work_units"]["units"] and len(resumed["pending_ids"]) == 6
    assert previous == frozen
    assert experiment_policy(SAVED_UNITS_EXPERIMENT)["request_sequence"] == ["generation"] * 3 + ["semantic_review"]


def test_four_atomic_calls_and_one_held_whole_review(app, stopped_add):
    before, previous = stopped_add
    calls = []
    async def stream(messages, schemas):
        names = {s["function"]["name"] for s in schemas}
        calls.append((names, deepcopy(messages)))
        record = app.unified.store.latest(before.id)
        if "submit_plan_delta" in names:
            assert names == {"submit_plan_delta", "ask_user"}
            assert "repair_agenda" not in messages[0]["content"]
            name, raw = "submit_plan_delta", offline_delta(record)
        else:
            assert names == {"submit_plan_review"}
            name, raw = "submit_plan_review", offline_held_review(json.loads(messages[-1]["content"]))
        async for event in tool_chunks(name, raw):
            yield event
    app.settings.stream = stream
    collect(app, before, previous)
    saved = app.unified.store.latest(before.id)
    assert len(calls) == 5, saved["report"]
    assert saved["metrics"]["provider_calls"] == 5
    assert all_units_complete(saved)
    assert len(saved["work_units"]["checkpoints"]) == 5
    assert saved["work_units"]["checkpoints"][:1] == previous["work_units"]["checkpoints"]
    assert saved["compilations"][:1] == previous["compilations"]
    assert saved["prior_work_units"] == previous["work_units"]
    assert len(saved["reviews"]) == 1 and saved["harness_run"]["decision"] == "hold"
    assert saved["status"] == "needs_resolution" and saved["generation_pause_reason"] == "experiment_review_complete"
    assert saved["metrics"]["budget"] == previous["metrics"]["budget"]
    assert saved["generation_progress"]["checkpoint_count"] == 5
    assert app.unified.store.get(previous["id"]) == previous
    assert app.db.get(before.id) == before


@pytest.mark.parametrize("pin", ["project_id", "predecessor_candidate_id", "predecessor_record_hash",
                                 "schedule_hash", "canonical_revision", "canonical_hash"])
def test_changed_caller_pin_fails_before_save_or_dispatch(app, stopped_add, pin):
    before, previous = stopped_add
    selected = argument(before, previous)
    selected[pin] = "changed"
    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider allowed")
        yield
    app.settings.stream = forbidden
    assert any(e["type"] == "error" for e in collect(app, before, previous, selected=selected))
    assert app.unified.store.latest(before.id) == previous
    assert app.db.get(before.id) == before


@pytest.mark.parametrize("damage", ["pending", "completed", "checkpoint", "source", "coverage", "compiler",
                                    "router", "sizing", "input", "review", "canonical"])
def test_rehashed_tampering_still_fails_closed(app, stopped_add, damage):
    before, previous = stopped_add
    content = previous["input"]
    if damage in {"pending", "completed"}:
        unit = previous["work_units"]["units"][int(damage == "pending")]
        unit["changes"][0]["intent"] += "tampered"
        unit["hash"] = _hash(unit["changes"])
    elif damage == "checkpoint":
        previous["work_units"]["checkpoints"][0]["delta_hash"] = "tampered"
    elif damage == "source":
        previous["project"]["plan_contract"]["sources"][0]["text"] += "tampered"
    elif damage == "coverage":
        previous["work_units"]["units"][-1]["changes"] = []
    elif damage == "compiler":
        previous["compilations"][0]["ir"]["summary"] += "tampered"
    elif damage == "router":
        previous["tool_attempts"][0]["raw_arguments"] = '{"changes":[]}'
    elif damage == "sizing":
        previous["work_units"]["units"][1]["estimated_text_bytes"] += 1
    elif damage == "input":
        content += "new direction"
    elif damage == "review":
        previous["reviews"] = [{"fabricated": True}]
    else:
        before.target_draft = "tampered"
        with app.db.connect() as connection:
            app.db.write_project(connection, before, expected_revision=before.revision)
    previous = app.unified.store.save(previous)
    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider allowed")
        yield
    app.settings.stream = forbidden
    events = collect(app, before, previous, content=content)
    assert any(e["type"] == "error" for e in events), events
    assert app.unified.store.latest(before.id) == previous
    assert app.db.get(before.id) == before


@pytest.mark.parametrize("failure", ["provider", "output", "invalid_delta", "router", "second_tool"])
def test_no_early_checkpoint_on_first_response_failure(app, stopped_add, failure):
    before, previous = stopped_add
    calls = []
    async def stream(messages, schemas):
        calls.append(messages)
        if failure == "provider":
            raise TimeoutError("offline")
        if failure == "output":
            yield {"type": "text", "text": "x" * 98305}
        else:
            name = "schedule_plan_changes" if failure == "router" else "submit_plan_delta"
            raw = offline_delta(app.unified.store.latest(before.id)) if failure == "second_tool" else {"target": "invalid unit"}
            async for event in tool_chunks(name, raw):
                yield event
            if failure == "second_tool":
                async for event in tool_chunks("submit_plan_delta", {"target": "extra"}):
                    yield {**event, "index": 1}
    app.settings.stream = stream
    collect(app, before, previous)
    saved = app.unified.store.latest(before.id)
    assert len(calls) == 1 and saved["metrics"]["provider_calls"] == 1
    assert saved["work_units"]["checkpoints"] == previous["work_units"]["checkpoints"]
    assert saved["compilations"] == previous["compilations"] and not saved["reviews"]
    assert saved["generation_progress"]["checkpoint_count"] == 1
    assert app.unified.store.get(previous["id"]) == previous
    assert app.db.get(before.id) == before


def test_dispatch_sequence_and_unchanged_bounds():
    policy = experiment_policy(PENDING_ARCHITECTURE_EXPERIMENT)
    assert policy["limits"] == {"max_calls": 5, "max_request_bytes": 163840,
        "max_total_input_bytes": 393216, "max_output_bytes": 98304, "call_timeout_seconds": 180}
    selected = {"project_id": "offline", **policy}
    for index in range(6):
        for stage, tool in [("generation", "submit_plan_delta"), ("router", "schedule_plan_changes"),
                            ("semantic_review", "submit_plan_review")]:
            rejection = request_stage_rejection({"planning_experiment": selected, "provider_calls": index},
                [{"function": {"name": tool}}], "semantic_review" if stage == "semantic_review" else "generation")
            assert (rejection is None) == (index < 5 and policy["request_sequence"][index] == stage)


@pytest.mark.parametrize("damage", ["target_draft", "new_target", "unrelated_architecture",
                                    "old_timestamp", "unrelated_binding"])
def test_forged_result_with_rehashed_audit_and_caller_pins_is_rejected(stopped_add, damage):
    """Self-consistent hashes do not prove that the accepted compiler produced a result."""
    from evograph.application.plan_ir import compile_plan_delta
    from evograph.application.plan_patch import _completed_compiler_audit
    from evograph.application.plan_units import _identity

    before, previous = stopped_add
    after = Project.model_validate(previous["project"])
    if damage == "target_draft":
        after.target_draft = "Forged target outside accepted checkpoint"
    elif damage == "new_target":
        after.targets[-1].statement = "Forged published target inside accepted checkpoint"
    elif damage == "unrelated_architecture":
        after.architectures[-1].risks.append("Forged unrelated architecture field")
    elif damage == "old_timestamp":
        after.plans[0].created_at = "2000-01-01T00:00:00+00:00"
    else:
        after.plan_contract.bindings[0].mechanism += " Forged unrelated contract mechanism"
    previous["project"] = after.model_dump()
    previous["candidate_hash"] = candidate_hash(after)
    previous["work_units"]["expected_fingerprint"] = _identity(after)
    accepted = next(a for a in previous["tool_attempts"]
                    if a["name"] == "submit_plan_delta" and a["status"] == "succeeded")
    historical = deepcopy(after.plan_contract.sources[-1].activity[:previous["tool_attempts"].index(accepted) + 1])
    historical[-1]["status"] = "started"
    base = before.model_copy(deep=True)
    base.plan_contract.sources.append(after.plan_contract.sources[-1].model_copy(deep=True))
    base.plan_contract.sources[-1].activity = deepcopy(historical)
    after.plan_contract.sources[-1].activity = deepcopy(historical)
    compiled = compile_plan_delta(base, json.loads(accepted["raw_arguments"]))
    previous["compilations"][0] = _completed_compiler_audit(compiled.audit, base, after, changed=True)
    previous["work_units"]["checkpoints"][0]["candidate_hash"] = candidate_hash(after)
    frozen = deepcopy(previous)
    with pytest.raises(ValueError, match="saved planning result differs from compiled checkpoint"):
        admit_pending_continuation(argument(before, previous), before, previous, previous["input"])
    assert previous == frozen
