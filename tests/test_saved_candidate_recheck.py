"""Exact frozen candidate offline replay. Mock acceptance proves plumbing only."""
import asyncio
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_recheck import (
    REVIEW_BUDGET,
    recheck_offer,
    recheck_pins,
    review_projection,
    validate_recheck,
)
from evograph.application.plan_units import all_units_complete
from evograph.domain.models import PendingQuestion, Project, now, uid
from evograph.infrastructure.database import ConflictError, Database
from evograph.infrastructure.plan_candidates import CandidateStore
from test_agent_stream import tool_chunks
from test_bounded_saved_units import offline_held_review
from test_review_materiality import batch_for


@pytest.fixture
def complete(app):
    fixtures = Path(__file__).parent / "fixtures"
    provenance = json.loads((fixtures / "qa55-complete-review-provenance.json").read_text())
    values = {}
    for name, info in provenance["files"].items():
        raw = gzip.decompress((fixtures / info["compressed_file"]).read_bytes())
        assert len(raw) == info["bytes"] and hashlib.sha256(raw).hexdigest() == info["sha256"]
        values[name] = json.loads(raw)
    before = Project.model_validate(values["project.json"])
    candidate = values["candidates.json"]
    app.db.create(before)
    assert app.unified.store.save(candidate) == candidate
    receipt = values["receipt.json"]
    with app.db.connect() as connection:
        connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
            receipt["id"], before.id, receipt["kind"], receipt["detail"], receipt["created_at"]))
    assert candidate["id"] == "8a28bc95ffd849f8"
    assert all_units_complete(candidate) and len(candidate["work_units"]["checkpoints"]) == 5
    assert candidate["metrics"]["provider_calls"] == 5 and candidate["reviews"] == []
    assert candidate["harness_run"]["decision"] == "hold"
    return before, candidate


def collect(app, before, candidate, *, pins=None, **kw):
    async def run():
        return [e async for e in app.agent.stream(before.id, "重新评审",
            review_recheck=pins or recheck_pins(before, candidate), **kw)]
    return asyncio.run(run())


def fake_provider(app, *, outcome="held", hook=None):
    calls = []
    async def stream(messages, schemas, **kwargs):
        assert {s["function"]["name"] for s in schemas} == {"submit_plan_review"}
        calls.append((deepcopy(messages), deepcopy(kwargs)))
        if hook:
            hook()
        if outcome == "error":
            raise ConnectionError("Offline provider failure")
        if outcome == "cancel":
            raise asyncio.CancelledError()
        if outcome == "invalid":
            yield {"type": "text", "text": "invalid offline protocol"}
        else:
            packet = json.loads(messages[-1]["content"])
            value = offline_held_review(packet) if outcome == "held" else batch_for(packet).model_dump()
            async for event in tool_chunks("submit_plan_review", value):
                yield event
        yield {"type": "usage", "tokens": 17}
    app.settings.stream = stream
    return calls


def assert_original_preserved(saved, original):
    for key, value in original.items():
        if key != "status":
            assert saved[key] == value, key
    assert "project" not in saved["review_attempts"][-1]["audit"]
    assert "work_units" not in saved["review_attempts"][-1]["audit"]


@pytest.mark.parametrize("outcome", ["held", "invalid", "error"])
def test_frozen_candidate_exact_one_call_and_original_audit_preserved(app, complete, outcome):
    before, original = complete
    calls = fake_provider(app, outcome=outcome)
    messages = app.db.messages(before.id)
    events = collect(app, before, original)
    saved = app.unified.store.latest(before.id)
    attempt = saved["review_attempts"][-1]
    current = review_projection(saved)
    assert len(calls) == current["metrics"]["provider_calls"] == 1
    assert current["metrics"]["budget"] == REVIEW_BUDGET
    assert "planning_experiment" not in current["metrics"]
    assert current["metrics"]["review_attempt_id"] == attempt["id"] != original["id"]
    assert attempt["closed_at"] and attempt["previous_attempt_id"] == original["id"]
    assert current["harness_run"]["decision"] == "hold"
    assert current["harness_run"]["run_id"] != original["harness_run"]["run_id"]
    assert app.db.get(before.id) == before and app.db.messages(before.id) == messages
    assert_original_preserved(saved, original)
    assert events[0]["turn_id"] == attempt["id"]
    assert events[-1]["changed"] is False
    assert app.agent.turn_result(before.id, attempt["id"])["summary"] is not None
    assert app.projects.get(before.id)["plan_candidate"]["review_recheck"] is not None


def test_repeated_explicit_rechecks_are_distinct_no_automatic_retry(app, complete):
    before, original = complete
    calls = fake_provider(app)
    collect(app, before, original)
    first = app.unified.store.latest(before.id)
    collect(app, before, first)
    second = app.unified.store.latest(before.id)
    assert len(calls) == 2 and len(second["review_attempts"]) == 2
    assert first["review_attempts"][0] == second["review_attempts"][0]
    assert_original_preserved(second, original)


def test_fresh_mock_pass_uses_atomic_commit_and_accounts_each_attempt_once(app, complete):
    before, original = complete
    fake_provider(app)
    collect(app, before, original)
    first = app.unified.store.latest(before.id)
    calls = fake_provider(app, outcome="pass")
    events = collect(app, before, first)
    saved = app.unified.store.latest(before.id)
    projected = review_projection(saved)
    assert len(calls) == 1 and events[-1]["changed"], events[-1]
    assert saved["status"] == "applied" and projected["harness_commit_replay"]["run"]["decision"] == "apply"
    assert_original_preserved(saved, original)
    canonical = app.db.get(before.id)
    assert canonical.revision == before.revision + 1
    assert canonical.metrics["model_tokens"] == before.metrics["model_tokens"] + original["metrics"]["tokens"] + 34
    assert canonical.metrics["planning_seconds"] == pytest.approx(before.metrics["planning_seconds"] +
        original["metrics"]["elapsed_seconds"] + sum(a["audit"]["metrics"]["elapsed_seconds"] for a in saved["review_attempts"]))
    assert app.projects.get(before.id)["plan_candidate"]["review_recheck"] is None


@pytest.mark.parametrize("damage", ["stale", "incomplete", "question", "archived", "busy", "terminal", "hash",
                                    "ready_fingerprint", "checkpoint", "old_pin", "running_harness", "open_attempt"])
def test_ineligible_and_changed_candidates_make_zero_calls(app, complete, damage):
    before, original = complete
    pins = recheck_pins(before, original)
    record = deepcopy(original)
    if damage == "stale":
        before.description = "New canonical state"
        before = app.db.save(before, "test")
    elif damage == "incomplete":
        record["work_units"]["units"][-1]["state"] = "pending"
    elif damage == "question":
        record["project"]["question"] = PendingQuestion(prompt="Choose").model_dump()
    elif damage == "archived":
        before.archived = True
        before = app.db.save(before, "test")
    elif damage == "busy":
        record["status"] = "reviewing"
    elif damage == "terminal":
        record["status"] = "applied"
    elif damage == "hash":
        record["project"]["target_draft"] = "Changed plan"
    elif damage == "ready_fingerprint":
        record["generation_progress"]["ready_planning_fingerprint"] = "bad"
    elif damage == "checkpoint":
        record["work_units"]["checkpoints"][-1]["delta_hash"] = "bad"
    elif damage == "old_pin":
        pins["record_hash"] = "bad"
    elif damage == "running_harness":
        record["harness_run"]["status"] = "running"
    elif damage == "open_attempt":
        app.unified.store.begin_review_attempt(before.id, pins, uid())
        record = app.unified.store.latest(before.id)
    if damage != "open_attempt":
        record = app.unified.store.save(record)
    if damage != "old_pin":
        pins = recheck_pins(before, record)
    calls = fake_provider(app)
    collect(app, before, record, pins=pins)
    assert not calls
    assert app.unified.store.get(original["id"]) == record


@pytest.mark.parametrize("change", ["discard", "canonical", "newer_candidate"])
def test_late_valid_response_cannot_apply_over_newer_state(app, complete, change):
    before, original = complete
    other = CandidateStore(Database(app.db.path))
    captured = {}
    def race():
        if change == "discard":
            other.discard(before.id, original["id"])
        elif change == "canonical":
            p = app.db.get(before.id)
            p.description = "Changed while model was running"
            app.db.save(p, "concurrent")
        else:
            fresh = deepcopy(original)
            fresh.update(id=uid(), created_at=now())
            other.save(fresh)
        captured["canonical"] = app.db.get(before.id)
        captured["candidate"] = other.get(original["id"])
    calls = fake_provider(app, outcome="pass", hook=race)
    events = collect(app, before, original)
    assert len(calls) == 1 and not events[-1]["changed"]
    assert app.db.get(before.id) == captured["canonical"]
    assert events[-1]["summary"]["candidate_outcome"]["canonical_unchanged"] is (change != "canonical")
    assert other.get(original["id"]) == captured["candidate"]
    assert any(e["kind"] == "candidate_terminal_audit" for e in app.db.events(before.id))


def test_cancel_closes_attempt_preserves_raw_audit_and_candidate(app, complete):
    before, original = complete
    calls = fake_provider(app, outcome="cancel")
    with pytest.raises(asyncio.CancelledError):
        collect(app, before, original)
    saved = app.unified.store.latest(before.id)
    assert len(calls) == 1 and saved["review_attempts"][-1]["closed_at"]
    assert_original_preserved(saved, original)
    assert review_projection(saved)["harness_run"]["status"] == "cancelled"
    assert app.db.get(before.id) == before and not app.agent.active_turns


def test_stale_save_and_duplicate_admission_rejected_between_connections(app, complete):
    before, original = complete
    other = CandidateStore(Database(app.db.path))
    pins = recheck_pins(before, original)
    _, active = app.unified.store.begin_review_attempt(before.id, pins, uid())
    with pytest.raises(ValueError):
        other.begin_review_attempt(before.id, pins, uid())
    with pytest.raises(ConflictError):
        other.save(original)
    newer = app.unified.store.save_review_attempt(active)
    with pytest.raises(ConflictError):
        other.save_review_attempt(active)
    assert other.get(original["id"])["review_attempts"][-1]["write_version"] == newer["review_attempt"]["write_version"]
    assert recheck_offer(before, other.get(original["id"])) is None


def test_ui_pins_use_exact_frozen_identity(app, complete):
    before, original = complete
    offer = app.projects.get(before.id)["plan_candidate"]["review_recheck"]
    assert offer == recheck_pins(before, original)
    assert validate_recheck(before, original, offer).revision == 7


def test_unclosed_origin_stage_rejected_before_any_call(app, complete):
    before, original = complete
    with app.db.connect() as connection:
        connection.execute("DELETE FROM events WHERE project_id=? AND kind='agent_turn_finished'", (before.id,))
    calls = fake_provider(app)
    collect(app, before, original)
    assert calls == [] and app.unified.store.latest(before.id) == original


@pytest.mark.parametrize("outcome", ["held", "cancel"])
def test_native_bridge_rechecks_same_candidate_in_new_turn_and_delivers_receipt(app, complete, outcome):
    import threading

    from evograph.transport.desktop import DesktopBridge

    before, original = complete
    calls = fake_provider(app, outcome=outcome)
    done = threading.Event()
    events = []
    class Window:
        def evaluate_js(self, script):
            event = json.loads(script.split("{detail: ", 1)[1].rsplit("}))", 1)[0])["event"]
            events.append(event)
            if event["type"] == "done":
                done.set()
    bridge = DesktopBridge(app)
    bridge._window = Window()
    bridge.start_agent("offline-native-review", {"project_id": before.id, "content": "重新评审",
        "review_recheck": recheck_pins(before, original), "snapshot_mode": "compact-v1"})
    assert done.wait(5)
    assert len(calls) == 1 and events[0]["type"] == "started"
    assert events[0]["turn_id"] != original["id"]
    assert events[-1]["summary"]["turn_id"] == events[0]["turn_id"]
    candidate = events[-1]["project"]["plan_candidate"]
    assert candidate["id"] == original["id"] and candidate["review_attempt"]["closed_at"]
    assert candidate["review_recheck"] is not None and app.db.get(before.id) == before


def test_http_recheck_routes_same_explicit_attempt_without_generation(app, complete):
    from evograph.transport.http import create_app
    from fastapi.testclient import TestClient

    before, original = complete
    calls = fake_provider(app)
    with TestClient(create_app(app)) as client:
        response = client.post("/api/agent/stream", json={"project_id": before.id,
            "content": "重新评审", "review_recheck": recheck_pins(before, original)})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert len(calls) == 1 and events[-1]["summary"]["turn_id"] == events[0]["turn_id"]
    assert not events[-1]["changed"] and app.db.get(before.id) == before


def test_recheck_preserves_project_scoped_review_controls(app, complete, monkeypatch):
    before, original = complete
    monkeypatch.setenv("EVOGRAPH_REVIEW_REASONING_EFFORT", "low")
    monkeypatch.setenv("EVOGRAPH_REVIEW_PROJECT_ID", before.id)
    calls = fake_provider(app)
    collect(app, before, original)
    assert calls[0][1] == {"request_controls": {"reasoning_effort": "low"}}
    saved = app.unified.store.latest(before.id)
    assert "harness_snapshots" not in saved["review_attempts"][-1]["audit"]
    assert saved["review_attempts"][-1]["audit"]["harness_snapshot_refs"] == [original["harness_run"]["snapshot_id"]]


def test_native_stream_closed_before_review_can_reopen_without_spending_a_call(app, complete):
    before, original = complete
    calls = fake_provider(app)
    async def stop_before_review():
        stream = app.agent.stream(before.id, "重新评审", review_recheck=recheck_pins(before, original))
        first = await anext(stream)
        assert first["type"] == "started"
        await stream.aclose()
    asyncio.run(stop_before_review())
    stopped = app.unified.store.latest(before.id)
    assert calls == [] and stopped["review_attempts"][-1]["closed_at"]
    assert review_projection(stopped)["metrics"]["provider_calls"] == 0
    assert app.projects.get(before.id)["plan_candidate"]["review_recheck"] is not None
    collect(app, before, stopped)
    assert len(calls) == 1 and len(app.unified.store.latest(before.id)["review_attempts"]) == 2
    assert app.db.get(before.id) == before


def test_unique_snapshot_in_current_attempt_is_not_replaced_by_dangling_reference(app, complete):
    before, original = complete
    original.pop("harness_snapshots")
    original = app.unified.store.save(original)
    calls = fake_provider(app)
    collect(app, before, original)
    saved = app.unified.store.latest(before.id)
    audit = saved["review_attempts"][-1]["audit"]
    assert len(calls) == 1 and len(audit["harness_snapshots"]) == 1
    assert audit["harness_snapshot_refs"] == []
    assert app.projects.get(before.id)["plan_candidate"]["review_recheck"] is not None
    collect(app, before, saved)
    second = app.unified.store.latest(before.id)
    assert second["review_attempts"][0] == saved["review_attempts"][0]
    assert "harness_snapshots" not in second["review_attempts"][-1]["audit"]
    assert len(review_projection(second)["harness_snapshots"]) == 1
