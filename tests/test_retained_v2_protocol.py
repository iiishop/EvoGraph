"""Offline v2 guard compatibility, never live reviewer-quality evidence."""
import asyncio
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_harness import _semantic_result, seal_snapshot
from evograph.application.plan_recheck import recheck_offer, recheck_pins, review_projection
from evograph.application.plan_review import (
    BATCH_VERSION,
    BatchSemanticReview,
    batch_review_certificate,
    batch_review_packet,
    normalize_batch_review,
    validate_batch_certificate,
)
from evograph.domain.models import Project
from test_agent_stream import tool_chunks
from test_bounded_planning_job import seven
from test_review_materiality import batch_for, context
from test_saved_candidate_recheck import (
    test_native_stream_closed_before_review_can_reopen_without_spending_a_call,
    test_stale_save_and_duplicate_admission_rejected_between_connections,
    test_unique_snapshot_in_current_attempt_is_not_replaced_by_dangling_reference,
)

__all__ = ["context", "seven", "test_late_valid_response_cannot_apply_over_newer_state",
           "test_native_stream_closed_before_review_can_reopen_without_spending_a_call",
           "test_stale_save_and_duplicate_admission_rejected_between_connections",
           "test_unique_snapshot_in_current_attempt_is_not_replaced_by_dangling_reference"]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "cross_partition"])
def test_v2_retained_status_partition_remains_exact(context, mutation):
    project, _, _, packet = context
    batch = batch_for(packet)
    subject = next(s for s in packet["required_subjects"] if s.startswith("retained_acceptance:"))
    if mutation == "missing":
        batch.statuses.supported.remove(subject)
    elif mutation == "extra":
        batch.statuses.supported.append("retained_acceptance:forged")
    elif mutation == "duplicate":
        batch.statuses.supported.append(subject)
    else:
        batch.statuses.unknown.append(subject)
    with pytest.raises(ValueError):
        normalize_batch_review(project, packet, batch)


def test_v2_schema_parser_and_raw_certificate_gates(context):
    project, _, snapshot, packet = context
    schema = BatchSemanticReview.model_json_schema()
    assert BATCH_VERSION == packet["review_protocol"] == "semantic-batch/v2"
    assert "statuses" in schema["required"] and "coverage" not in schema["properties"]
    batch = batch_for(packet)
    raw = batch.model_dump_json()
    assert BatchSemanticReview.model_validate_json(raw) == batch
    review = normalize_batch_review(project, packet, batch)
    certificate = batch_review_certificate(raw, batch)
    assert _semantic_result(snapshot, review, certificate)[0].verdict == "pass"
    invalid = deepcopy(certificate)
    invalid["schema_version"] = "semantic-batch/v3"
    with pytest.raises(ValueError, match="obsolete"):
        _semantic_result(snapshot, review, invalid)
    invalid = deepcopy(certificate)
    invalid["batch"]["summary"] = "Changed normalized certificate"
    with pytest.raises(ValueError, match="differ"):
        _semantic_result(snapshot, review, invalid)
    v3 = batch.model_dump()
    v3["coverage"] = {s: "supported" for s in v3.pop("statuses")["supported"]}
    with pytest.raises(ValueError):
        BatchSemanticReview.model_validate_json(json.dumps(v3))


def test_legacy_packet_snapshot_and_raw_v2_certificate_remain_exact_but_obsolete():
    raw = (FIXTURES / "legacy-v2-review-certificate.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "479c56dcae35297aa284b76181f8fd6c7b3f260a4b8148bdd704eabb857d2728"
    old = json.loads(raw)
    project, record = Project.model_validate(old["project"]), old["record"]
    assert batch_review_packet(project, project, record) == old["packet"]
    assert seal_snapshot(project, project, record).model_dump() == old["snapshot"]
    certificate = old["certificate"]
    batch = BatchSemanticReview.model_validate_json(certificate["raw_arguments"])
    assert batch_review_certificate(certificate["raw_arguments"], batch) == certificate
    with pytest.raises(ValueError, match="版本已更新"):
        validate_batch_certificate(project, project, record)


def test_real_invalid_v2_output_stays_invalid_with_unchanged_frozen_bytes():
    record = json.loads(gzip.decompress((FIXTURES / "qa55-complete-candidate.json.gz").read_bytes()))
    project = Project.model_validate(record["project"])
    before = Project.model_validate_json(gzip.decompress((FIXTURES / "qa55-complete-canonical.json.gz").read_bytes()))
    packet = batch_review_packet(before, project, record)
    raw = (FIXTURES / "qa55-invalid-review-output.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "b1ee33fd71d38761eab9bf3881e18334ec5b4b21f03aaaef3e19e4b08d252052"
    batch = BatchSemanticReview.model_validate_json(raw)
    assert batch.review_scope_hash == packet["review_scope_hash"]
    statuses = {s for values in batch.statuses.model_dump().values() for s in values}
    assert set(packet["required_subjects"]) - statuses == {
        "slice_activation:slice-5-reminder-email", "slice_activation:slice-7-notification-reliability"}
    with pytest.raises(ValueError, match="精确覆盖"):
        normalize_batch_review(project, packet, batch)


@pytest.fixture
def complete(app, seven):
    generated = []
    async def stop_review(messages, schemas, **kwargs):
        names = {s["function"]["name"] for s in schemas}
        generated.append(names)
        if names == {"submit_plan_review"}:
            yield {"type": "text", "text": "Offline invalid review, preserve complete candidate"}
            return
        if "schedule_plan_changes" in names:
            name, raw = "schedule_plan_changes", {"changes": [{"kind": "contract", "id": "check0",
                "fields": ["statement"], "uses": [], "intent": "Clarify exact existing acceptance"}]}
        else:
            assert "submit_plan_delta" in names
            name, raw = "submit_plan_delta", {"contracts": [{"key": "check0",
                "statement": "Report broken local Markdown links without writes in section zero"}]}
        async for event in tool_chunks(name, raw):
            yield event
    app.settings.stream = stop_review
    async def generate():
        return [e async for e in app.agent.stream(seven.id, "Clarify check0 while preserving its acceptance.")]
    asyncio.run(generate())
    original = app.unified.store.latest(seven.id)
    assert len(generated) == 3 and original["status"] == "needs_resolution"
    return seven, original


@pytest.mark.parametrize("outcome", ["valid", "invalid", "cancel"])
def test_current_guard_candidate_rechecks_in_v2_without_generation(app, complete, outcome):
    seven, original = complete
    pins = recheck_offer(seven, original)
    assert pins is not None
    calls = []
    async def review_only(messages, schemas, **kwargs):
        assert {s["function"]["name"] for s in schemas} == {"submit_plan_review"}
        assert "statuses" in schemas[0]["function"]["parameters"]["required"]
        packet = json.loads(messages[-1]["content"])
        assert packet["review_protocol"] == "semantic-batch/v2"
        assert len([s for s in packet["required_subjects"] if s.startswith("retained_acceptance:")]) == 7
        calls.append(packet)
        if outcome == "cancel":
            raise asyncio.CancelledError()
        if outcome == "invalid":
            yield {"type": "text", "text": "Offline invalid recheck"}
        else:
            async for event in tool_chunks("submit_plan_review", batch_for(packet).model_dump()):
                yield event
    app.settings.stream = review_only
    async def recheck():
        return [e async for e in app.agent.stream(seven.id, "重新评审", review_recheck=pins)]
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(recheck())
        events = []
    else:
        events = asyncio.run(recheck())
    saved = app.unified.store.latest(seven.id)
    current = review_projection(saved)
    assert len(calls) == current["metrics"]["provider_calls"] == 1, events
    assert saved["retained_acceptance"] == original["retained_acceptance"]
    assert saved["compilations"] == original["compilations"]
    assert saved["work_units"] == original["work_units"]
    assert saved["review_attempts"][-1]["closed_at"]
    if outcome == "valid":
        assert saved["status"] == "applied"
        assert current["batch_reviews"][-1]["schema_version"] == "semantic-batch/v2"
        assert app.db.get(seven.id).revision == seven.revision + 1
    else:
        assert app.db.get(seven.id) == seven
        assert current["harness_run"]["decision"] == "hold"


def test_current_guard_stale_review_pin_never_dispatches(app, complete):
    before, original = complete
    calls = []
    async def never(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Stale pin must not reach provider")
        yield
    app.settings.stream = never
    pins = recheck_pins(before, original)
    pins["record_hash"] = "stale"
    async def run():
        return [e async for e in app.agent.stream(before.id, "重新评审", review_recheck=pins)]
    events = asyncio.run(run())
    assert not calls and any(e["type"] == "error" for e in events)
    assert app.unified.store.get(original["id"]) == original
    assert app.db.get(before.id) == before


@pytest.mark.parametrize("controls", [None, {"reasoning_effort": "low"}])
def test_current_guard_review_controls_and_snapshot_references(app, complete, monkeypatch, controls):
    from evograph.application import unified_planning
    before, original = complete
    # Inject an explicit control value; do not inspect launcher environment/settings.
    monkeypatch.setattr(unified_planning, "_review_request_controls", lambda project_id: controls)
    seen = []
    async def invalid(messages, schemas, **kwargs):
        seen.append(kwargs)
        yield {"type": "text", "text": "Offline invalid review"}
    app.settings.stream = invalid
    async def run():
        return [e async for e in app.agent.stream(before.id, "重新评审", review_recheck=recheck_pins(before, original))]
    asyncio.run(run())
    assert seen == ([{}] if controls is None else [{"request_controls": controls}])
    saved = app.unified.store.latest(before.id)
    audit = saved["review_attempts"][-1]["audit"]
    assert "harness_snapshots" not in audit
    assert audit["harness_snapshot_refs"] == [original["harness_run"]["snapshot_id"]]
    assert app.db.get(before.id) == before


def test_legacy_unknown_entry_cannot_be_re_admitted_or_rechecked_as_current(app):
    from evograph.application.plan_recheck import validate_recheck
    from evograph.infrastructure.database import ConflictError
    candidate_path = FIXTURES / "qa55-complete-candidate.json.gz"
    original_bytes = candidate_path.read_bytes()
    record = json.loads(gzip.decompress(original_bytes))
    before = Project.model_validate_json(gzip.decompress((FIXTURES / "qa55-complete-canonical.json.gz").read_bytes()))
    assert "retained_acceptance" not in record
    original = deepcopy(record)
    app.db.create(before)
    with pytest.raises(ConflictError, match="pinned predecessor missing"):
        app.unified.store.save(record)
    assert record == original and app.unified.store.latest(before.id) is None
    # Restore an existing historical row exactly, without running new-entry admission.
    # This isolated fixture models already-durable legacy storage, not a migration.
    with app.db.connect() as connection:
        connection.execute("INSERT INTO plan_candidates VALUES(?,?,?,?)", (
            record["id"], before.id, json.dumps(record, ensure_ascii=False), record["created_at"]))
    assert app.unified.store.get(record["id"]) == original
    assert recheck_offer(before, record) is None
    with pytest.raises(ValueError, match="完整、未改变"):
        validate_recheck(before, record, recheck_pins(before, record))
    assert app.unified.store.get(record["id"]) == original
    assert candidate_path.read_bytes() == original_bytes


@pytest.mark.parametrize("change", ["discard", "canonical", "newer_candidate"])
def test_late_valid_response_cannot_apply_over_newer_state(app, complete, change):
    from evograph.infrastructure.database import Database
    from evograph.infrastructure.plan_candidates import CandidateStore
    from test_plan_patch import stage
    from test_saved_candidate_recheck import collect, fake_provider
    before, original = complete
    other = CandidateStore(Database(app.db.path))
    captured = {}
    def race():
        if change == "discard":
            other.discard(before.id, original["id"])
        elif change == "canonical":
            current = app.db.get(before.id)
            current.description = "Changed while model was running"
            app.db.save(current, "concurrent")
        else:
            # A real newly admitted synthetic candidate owns a new entry. Cloning
            # the old candidate's retained seal is correctly rejected by admission.
            stage(app, before, text="Separate new request while review was running.")
        captured["canonical"] = app.db.get(before.id)
        captured["candidate"] = other.get(original["id"])
    calls = fake_provider(app, outcome="pass", hook=race)
    events = collect(app, before, original)
    assert len(calls) == 1 and not events[-1]["changed"]
    assert app.db.get(before.id) == captured["canonical"]
    assert other.get(original["id"]) == captured["candidate"]
    assert any(e["kind"] == "candidate_terminal_audit" for e in app.db.events(before.id))
