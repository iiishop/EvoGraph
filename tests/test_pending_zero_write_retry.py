"""One pinned retry of the retained zero-write failure; all outputs are offline fakes."""
import gzip
import hashlib
import json
from copy import deepcopy

import pytest
from evograph.application.plan_pending_continuation import (
    ZERO_WRITE_RETRY,
    admit_pending_continuation,
)
from evograph.application.plan_units import _hash, all_units_complete
from evograph.domain.models import Project
from evograph.domain.plan_contracts import candidate_hash
from test_agent_stream import tool_chunks
from test_bounded_saved_units import offline_held_review
from test_pending_architecture_continuation import (
    FIXTURES,
    argument,
    collect,
    offline_delta,
    stopped_add,  # noqa: F401
)


@pytest.fixture
def zero_write(app, stopped_add):  # noqa: F811
    before, original = stopped_add
    provenance = json.loads((FIXTURES / "qa55-zero-write-successor-provenance.json").read_text())
    evidence = provenance["fixture"]
    raw = gzip.decompress((FIXTURES / evidence["compressed_file"]).read_bytes())
    assert len(raw) == evidence["bytes"] and hashlib.sha256(raw).hexdigest() == evidence["sha256"]
    failed = json.loads(raw)
    assert failed["id"] == provenance["candidate_id"] and _hash(failed) == provenance["record_hash"]
    assert app.unified.store.save(failed) == failed
    return before, original, failed


def retry_argument(before, failed):
    return {**argument(before, failed), "retry": ZERO_WRITE_RETRY}


def assert_blocked(app, before, failed, *, pins=None, content=None):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider allowed")
        yield
    app.settings.stream = forbidden
    events = collect(app, before, failed, selected=pins or retry_argument(before, failed), content=content)
    assert any(e["type"] == "error" for e in events), events
    assert app.unified.store.latest(before.id) == failed
    assert app.db.get(before.id) == before


def test_exact_retained_failure_preserves_all_original_pending_work(app, zero_write):
    before, original, failed = zero_write
    frozen = deepcopy((before, original, failed))
    schedule, admission = admit_pending_continuation(
        retry_argument(before, failed), before, failed, failed["input"], store=app.unified.store)
    assert schedule == failed["work_units"]
    assert schedule["pending_ids"] == ["unit-002", "unit-004", "unit-006", "unit-007"]
    assert schedule["units"][0] == original["work_units"]["units"][0]
    assert schedule["checkpoints"] == original["work_units"]["checkpoints"]
    assert schedule["manifest"] == original["work_units"]["manifest"]
    def rows(schedule):
        return sorted((r for u in schedule["units"] if u["state"] == "pending"
                       for r in u["changes"]), key=lambda r: r["change_id"])
    assert len(rows(schedule)) == 28 and rows(schedule) == rows(original["work_units"])
    assert admission["zero_write_retry"] == {
        "original_candidate_id": original["id"], "original_record_hash": _hash(original),
        "original_admission_hash": _hash(failed["pending_architecture_admission"])}
    assert len(json.dumps(admission).encode()) < 1800
    assert (before, original, failed) == frozen
    assert app.unified.store.get(original["id"]) == original


@pytest.mark.parametrize("pin", ["project_id", "predecessor_candidate_id", "predecessor_record_hash",
                                 "schedule_hash", "canonical_revision", "canonical_hash", "retry"])
def test_retry_requires_explicit_fresh_pins(app, zero_write, pin):
    before, original, failed = zero_write
    pins = retry_argument(before, failed)
    pins[pin] = "changed"
    assert_blocked(app, before, failed, pins=pins)


@pytest.mark.parametrize("damage", ["input", "canonical", "parent", "parent_checkpoint", "payload", "old_source",
    "activity", "new_source", "checkpoint", "pending", "manifest", "admission", "compilation",
    "success", "review", "nested", "pin_accepted"])
def test_rehashed_zero_write_tampering_rejected(app, zero_write, damage):
    before, original, failed = zero_write
    content = failed["input"]
    if damage == "input":
        content += "new direction"
    elif damage == "canonical":
        before.target_draft = "changed"
        with app.db.connect() as connection:
            app.db.write_project(connection, before, expected_revision=before.revision)
    elif damage == "parent":
        original["compilations"][0]["ir"]["summary"] += "changed"
        app.unified.store.save(original)
        failed["pending_architecture_admission"]["pins"]["predecessor_record_hash"] = _hash(original)
    elif damage == "parent_checkpoint":
        original["work_units"]["checkpoints"][0]["delta_hash"] = "forged"
        app.unified.store.save(original)
        failed["prior_work_units"] = deepcopy(original["work_units"])
        failed["pending_architecture_admission"]["pins"]["predecessor_record_hash"] = _hash(original)
        failed["pending_architecture_admission"]["pins"]["schedule_hash"] = _hash(original["work_units"])
    elif damage == "payload":
        failed["project"]["target_draft"] = "changed"
    elif damage == "old_source":
        failed["project"]["plan_contract"]["sources"][-2]["activity"] = []
    elif damage == "activity":
        failed["project"]["plan_contract"]["sources"][-1]["activity"] = []
    elif damage == "new_source":
        failed["project"]["plan_contract"]["sources"][-1]["evidence"] = [{"invented": True}]
    elif damage == "checkpoint":
        failed["work_units"]["checkpoints"][0]["delta_hash"] = "forged"
    elif damage == "pending":
        row = failed["work_units"]["units"][1]["changes"][0]
        row["intent"] += "changed"
        failed["work_units"]["units"][1]["hash"] = _hash(failed["work_units"]["units"][1]["changes"])
    elif damage == "manifest":
        failed["work_units"]["manifest"][0]["intent"] += "changed"
    elif damage == "admission":
        failed["pending_architecture_admission"]["compiler_lineage_hash"] = "changed"
    elif damage == "compilation":
        failed["compilations"].append(deepcopy(failed["compilations"][0]))
    elif damage == "success":
        failed["tool_attempts"][0]["status"] = "succeeded"
    elif damage == "review":
        failed["reviews"] = [{"invented": True}]
    elif damage == "nested":
        failed["pending_architecture_admission"]["pins"]["retry"] = ZERO_WRITE_RETRY
    else:
        failed["unit_request"]["accepted_delta_hash"] = "forged"
    failed = app.unified.store.save(failed)
    assert_blocked(app, before, failed, content=content)
    assert app.unified.store.get(original["id"]) == original


def test_old_snapshot_is_rejected_when_latest_record_changed(app, zero_write):
    before, original, failed = zero_write
    changed = deepcopy(failed)
    changed["report"]["findings"].append({"message": "new state"})
    app.unified.store.save(changed)
    with pytest.raises(ValueError, match="no longer latest"):
        admit_pending_continuation(retry_argument(before, failed), before, failed, failed["input"],
                                   store=app.unified.store)
    assert_blocked(app, before, changed, pins=retry_argument(before, failed))


def test_retry_has_four_atomic_generations_and_one_whole_review(app, zero_write):
    before, original, failed = zero_write
    calls, units = [], []
    async def stream(messages, schemas):
        calls.append(deepcopy(messages))
        names = {s["function"]["name"] for s in schemas}
        record = app.unified.store.latest(before.id)
        assert all(old not in messages[0]["content"] for old in ["raw_arguments", "pending_architecture_admission"])
        if "submit_plan_delta" in names:
            assert names == {"submit_plan_delta", "ask_user"}
            unit = next(u for u in record["work_units"]["units"] if u["state"] == "pending")
            units.append(unit["id"])
            assert messages[-1]["content"] == original["input"]
            name, raw = "submit_plan_delta", offline_delta(record)
        else:
            assert names == {"submit_plan_review"}
            name, raw = "submit_plan_review", offline_held_review(json.loads(messages[-1]["content"]))
        async for event in tool_chunks(name, raw):
            yield event
    app.settings.stream = stream
    collect(app, before, failed, selected=retry_argument(before, failed))
    saved = app.unified.store.latest(before.id)
    assert units == ["unit-002", "unit-004", "unit-006", "unit-007"]
    assert len(calls) == saved["metrics"]["provider_calls"] == 5, saved["report"]
    assert all_units_complete(saved)
    assert len(saved["work_units"]["checkpoints"]) == 5
    assert saved["work_units"]["checkpoints"][:1] == original["work_units"]["checkpoints"]
    assert saved["compilations"][:1] == original["compilations"]
    assert saved["prior_work_units"] == failed["work_units"]
    assert saved["project"]["plan_contract"]["sources"][:-1] == failed["project"]["plan_contract"]["sources"]
    assert saved["metrics"]["budget"] == failed["metrics"]["budget"]
    assert saved["status"] == "needs_resolution" and saved["harness_run"]["decision"] == "hold"
    assert app.unified.store.get(original["id"]) == original
    assert app.unified.store.get(failed["id"]) == failed
    assert app.db.get(before.id) == before
    assert_blocked(app, before, saved)


@pytest.mark.parametrize("failure", ["extra_identity", "second_tool"])
def test_first_invalid_output_stops_without_mutation_or_recursive_retry(app, zero_write, failure):
    before, original, failed = zero_write
    calls = []
    async def stream(messages, schemas):
        calls.append(messages)
        raw = (json.loads(failed["tool_attempts"][0]["raw_arguments"]) if failure == "extra_identity"
               else offline_delta(app.unified.store.latest(before.id)))
        async for event in tool_chunks("submit_plan_delta", raw):
            yield event
        if failure == "second_tool":
            async for event in tool_chunks("submit_plan_delta", {"target": "extra"}):
                yield {**event, "index": 1}
    app.settings.stream = stream
    collect(app, before, failed, selected=retry_argument(before, failed))
    saved = app.unified.store.latest(before.id)
    assert len(calls) == saved["metrics"]["provider_calls"] == 1
    assert saved["compilations"] == original["compilations"]
    assert saved["work_units"]["checkpoints"] == original["work_units"]["checkpoints"]
    assert saved["generation_progress"]["checkpoint_count"] == 1 and not saved["reviews"]
    project = Project.model_validate(saved["project"])
    project.plan_contract.sources.pop()
    assert candidate_hash(project) == failed["candidate_hash"]
    assert_blocked(app, before, saved)
    assert app.unified.store.get(original["id"]) == original
    assert app.unified.store.get(failed["id"]) == failed


def test_same_stage_without_retry_opt_in_and_ordinary_resume_remain_blocked(app, zero_write):
    before, original, failed = zero_write
    assert_blocked(app, before, failed, pins=argument(before, failed))
    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider allowed")
        yield
    app.settings.stream = forbidden
    import asyncio
    events = asyncio.run(_ordinary(app, before, failed))
    assert any(e["type"] == "error" for e in events)
    assert app.unified.store.latest(before.id) == failed


async def _ordinary(app, before, failed):
    return [event async for event in app.unified.stream(before.id, failed["input"])]


def test_original_stage_with_a_successful_write_is_ineligible(app, stopped_add):  # noqa: F811
    before, original = stopped_add
    calls = []
    async def stream(messages, schemas):
        calls.append(messages)
        raw = offline_delta(app.unified.store.latest(before.id)) if len(calls) == 1 else {"target": "invalid"}
        async for event in tool_chunks("submit_plan_delta", raw):
            yield event
    app.settings.stream = stream
    collect(app, before, original)
    written = app.unified.store.latest(before.id)
    assert len(calls) == 2 and len(written["work_units"]["checkpoints"]) == 2
    assert written["generation_pause_reason"] == "experiment_first_failure"
    assert_blocked(app, before, written)
    assert app.unified.store.get(original["id"]) == original
