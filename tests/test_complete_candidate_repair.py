"""Frozen closed-stage replay with fake providers; no semantic-quality claim."""
import asyncio
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.plan_recheck import review_projection
from evograph.application.plan_repair_phase import repair_phase_pins
from evograph.application.plan_units import _hash, _objects, all_units_complete
from evograph.domain.models import PendingQuestion, Project, now, uid
from evograph.infrastructure.database import ConflictError
from test_agent_stream import tool_chunks
from test_review_materiality import batch_for

FIXTURES = Path(__file__).parent / "fixtures"
FEEDBACK = ("Tester feedback: repair the three verified email failures: delivery ambiguity after timeout, "
            "concurrent duplicate sends, and reminder invalidation after rescheduling. "
            "Also clarify overlapping reminder, waitlist, and reliability milestone ownership. "
            "Keep original sources, requirements, single-shop scope and maintenance limits.")
BUDGET = {"max_calls": 5, "max_request_bytes": 163840, "max_review_request_bytes": 163840,
          "max_total_input_bytes": 393216, "max_output_bytes": 98304, "call_timeout_seconds": 180}


@pytest.fixture
def closed(app):
    provenance = json.loads((FIXTURES / "qa55-closed-repair-provenance.json").read_text())
    values = {}
    for name, expected in provenance["files"].items():
        raw = gzip.decompress((FIXTURES / expected["compressed_file"]).read_bytes())
        assert len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"]
        values[name] = json.loads(raw)
    before = Project.model_validate_json(gzip.decompress((FIXTURES / "qa55-complete-canonical.json.gz").read_bytes()))
    record = values["candidate"]
    app.db.create(before)
    assert app.unified.store.save(record) == record
    with app.db.connect() as db:
        for receipt in values["receipts"]:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (receipt["id"], before.id,
                receipt["kind"], receipt["detail"], receipt["created_at"]))
    assert record["id"] == "8a28bc95ffd849f8"
    assert record["review_attempts"][-1]["id"] == "4aa81da6d0184eee"
    assert record["review_attempts"][-1]["closed_at"]
    assert all_units_complete(record) and len(record["work_units"]["checkpoints"]) == 5
    assert record["metrics"]["provider_calls"] == 5
    assert review_projection(record)["metrics"]["provider_calls"] == 1
    return before, record


def collect(app, before, record, **kwargs):
    kwargs.setdefault("repair_from", repair_phase_pins(before, record))
    async def run():
        return [e async for e in app.agent.stream(before.id, FEEDBACK, **kwargs)]
    return asyncio.run(run())


def fake_provider(app, before, *, review="pass", hook=None):
    calls = []
    key = "contract-notify-idempotency-durability"
    async def stream(messages, schemas, **kwargs):
        names = {s["function"]["name"] for s in schemas}
        record = app.unified.store.latest(before.id)
        calls.append((names, deepcopy(messages)))
        if hook:
            hook(len(calls), record)
        if "schedule_plan_changes" in names:
            name, raw = "schedule_plan_changes", {"changes": [{"kind": "contract", "id": key,
                "fields": ["statement"], "uses": [], "intent": "Offline ordinary repair plumbing"}]}
            assert record["prior_work_units"]["completed_ids"] == [u["id"] for u in record["prior_work_units"]["units"]]
            assert record["inherited_semantic_findings"] is None
            assert record["inherited_findings"] == []
        elif "submit_plan_delta" in names:
            from evograph.application.plan_budget import compact_schema
            from evograph.application.plan_unit_schema import project_unit_schema
            projected, audit = project_unit_schema(record, Project.model_validate(record["project"]))
            assert audit["status"] == "projected"
            assert next(s["function"]["parameters"] for s in schemas if s["function"]["name"] == "submit_plan_delta") == compact_schema(projected)
            old = _objects(Project.model_validate(record["project"]))["contract:" + key]
            name, raw = "submit_plan_delta", {"summary": "Offline plumbing, semantics unassessed",
                "contracts": [{"key": key, "statement": old["statement"] + " Offline plumbing fixture."}]}
        else:
            assert names == {"submit_plan_review"}
            if review == "invalid":
                yield {"type": "text", "text": "Offline invalid output must not become findings"}
                return
            name = "submit_plan_review"
            packet = json.loads(messages[-1]["content"])
            raw = batch_for(packet).model_dump()
            if "coverage" in schemas[0]["function"]["parameters"]["properties"] and "statuses" in raw:
                raw["coverage"] = {subject: verdict for verdict, subjects in raw.pop("statuses").items()
                                   for subject in subjects}
        async for event in tool_chunks(name, raw):
            yield event
        yield {"type": "usage", "tokens": 7}
    app.settings.stream = stream
    return calls


@pytest.mark.parametrize("review", ["pass", "invalid"])
def test_normal_request_new_source_three_calls_original_audits_exact(app, closed, review):
    before, original = closed
    calls = fake_provider(app, before, review=review)
    pins = app.projects.get(before.id)["plan_candidate"]["repair_from"]
    assert pins == repair_phase_pins(before, original)
    events = collect(app, before, original, repair_from=pins)
    saved = app.unified.store.latest(before.id)
    assert [next(iter(n - {"ask_user"})) for n, _ in calls] == ["schedule_plan_changes", "submit_plan_delta", "submit_plan_review"], events
    assert saved["id"] != original["id"] and saved["resumes_candidate_id"] == original["id"]
    assert saved["repair_phase"]["pins"] == pins
    assert saved["repair_phase"]["predecessor_experiment"] == original["planning_experiment"]
    assert saved["repair_phase"]["closed_turns"][-1]["turn_id"] == original["review_attempts"][-1]["id"]
    assert not saved.get("planning_experiment") and not saved["metrics"].get("planning_experiment")
    assert saved["metrics"]["budget"] == BUDGET and saved["metrics"]["provider_calls"] == 3
    assert saved["prior_work_units"] == original["work_units"]
    assert saved["generation_progress"]["checkpoint_count"] == 6 and all_units_complete(saved)
    assert app.unified.store.get(original["id"]) == original
    assert saved["project"]["plan_contract"]["sources"][:-1] == original["project"]["plan_contract"]["sources"]
    source = saved["project"]["plan_contract"]["sources"][-1]
    assert source["id"] == saved["id"] and source["text"] == FEEDBACK and source["origin"] == "user"
    assert source["message_id"] and source["activity"]
    assert saved["project"]["plan_contract"]["requirements"] == original["project"]["plan_contract"]["requirements"]
    assert len(saved["compilations"]) == 1 and len(saved["work_units"]["checkpoints"]) == 1
    assert events[-1]["changed"] is (review == "pass")
    if review == "pass":
        assert saved["harness_commit_replay"]["run"]["decision"] == "apply"
    else:
        assert saved["reviews"] == [] and not saved.get("batch_reviews")
        assert app.db.get(before.id) == before
    assert original["reviews"] == [] and original["review_attempts"][-1]["audit"]["reviews"] == []
    with pytest.raises(ConflictError):
        app.unified.store.save(original)


@pytest.mark.parametrize("damage", ["missing_pin", "stale_pin", "newer_candidate", "stale_base", "archived", "question",
    "incomplete", "busy", "active_call", "open_attempt", "missing_receipt", "origin_receipt", "checkpoint", "provenance", "ready_fingerprint"])
def test_ineligible_inputs_never_create_phase_or_dispatch(app, closed, damage):
    before, original = closed
    record, pins = deepcopy(original), repair_phase_pins(before, original)
    if damage == "missing_pin":
        pins = None
    elif damage == "stale_pin":
        pins["record_hash"] = "old"
    elif damage in {"stale_base", "archived", "question"}:
        if damage == "stale_base":
            before.description += " changed"
        elif damage == "archived":
            before.archived = True
        else:
            before.question = PendingQuestion(prompt="Choose")
        app.db.save(before, "test")
    elif damage in {"missing_receipt", "origin_receipt"}:
        turn = original["turn_id"] if damage == "origin_receipt" else original["review_attempts"][-1]["id"]
        with app.db.connect() as db:
            db.execute("DELETE FROM events WHERE json_extract(detail, '$.turn_id')=?", (turn,))
    else:
        if damage == "newer_candidate":
            record.update(id=uid(), created_at=now())
        elif damage == "incomplete":
            record["work_units"]["units"][-1]["state"] = "pending"
        elif damage == "busy":
            record["status"] = "reviewing"
        elif damage == "active_call":
            record["metrics"]["calls"][-1]["status"] = "running"
        elif damage == "open_attempt":
            record["review_attempts"][-1]["closed_at"] = None
        elif damage == "checkpoint":
            record["work_units"]["checkpoints"][-1]["delta_hash"] = "changed"
        elif damage == "provenance":
            record["pending_architecture_admission"]["compiler_lineage_hash"] = "changed"
        elif damage == "ready_fingerprint":
            record["generation_progress"]["ready_planning_fingerprint"] = "changed"
        # Corrupt persisted input deliberately, bypassing normal write guards.
        with app.db.connect() as db:
            if damage == "newer_candidate":
                db.execute("INSERT INTO plan_candidates VALUES(?,?,?,?)", (record["id"], before.id, json.dumps(record), record["created_at"]))
            else:
                db.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (json.dumps(record), record["id"]))
        if damage != "newer_candidate":
            pins = repair_phase_pins(before, record)  # Fresh caller hash cannot bypass lineage checks.
    latest = app.unified.store.latest(before.id)
    calls = fake_provider(app, before)
    events = collect(app, before, original, repair_from=pins)
    assert calls == [], events
    assert app.unified.store.latest(before.id) == latest
    assert not events[-1]["changed"]


def test_second_connection_admission_race_rejected(app, closed, monkeypatch):
    before, original = closed
    store = app.unified.store
    save = store.save
    def race(data):
        if data.get("repair_phase"):
            with app.db.connect() as db:
                changed = deepcopy(original)
                changed["review_attempts"][-1]["audit"]["metrics"]["tokens"] += 1
                db.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (json.dumps(changed), original["id"]))
        return save(data)
    monkeypatch.setattr(store, "save", race)
    calls = fake_provider(app, before)
    events = collect(app, before, original)
    assert calls == [] and not events[-1]["changed"]
    assert store.latest(before.id)["id"] == original["id"]


def test_http_composer_carries_exact_offer_and_rejects_stale_tab(app, closed):
    from evograph.transport.http import create_app
    from fastapi.testclient import TestClient
    before, original = closed
    calls = fake_provider(app, before, review="invalid")
    request = {"project_id": before.id, "content": FEEDBACK,
               "repair_from": app.projects.get(before.id)["plan_candidate"]["repair_from"]}
    with TestClient(create_app(app)) as client:
        result = client.post("/api/agent/stream", json=request)
        assert result.status_code == 200 and len(calls) == 3
        saved_hash = _hash(app.unified.store.latest(before.id))
        second = client.post("/api/agent/stream", json=request)
        assert second.status_code == 200 and len(calls) == 3
    assert _hash(app.unified.store.latest(before.id)) == saved_hash
    assert app.unified.store.get(original["id"]) == original


@pytest.mark.parametrize("mutation", ["admission", "checkpoints", "sources", "experiment"])
def test_new_phase_cannot_mutate_immutable_lineage(app, closed, mutation):
    before, original = closed
    fake_provider(app, before, review="invalid")
    collect(app, before, original)
    saved = app.unified.store.latest(before.id)
    changed = deepcopy(saved)
    if mutation == "admission":
        changed["repair_phase"]["closed_turns"][0]["receipt_hash"] = "changed"
    elif mutation == "checkpoints":
        changed["prior_work_units"]["checkpoints"][-1]["delta_hash"] = "changed"
    elif mutation == "sources":
        changed["project"]["plan_contract"]["sources"][0]["text"] += " changed"
    else:
        changed["planning_experiment"] = original["planning_experiment"]
    with pytest.raises(ConflictError):
        app.unified.store.save(changed)
    assert app.unified.store.latest(before.id) == saved
    assert app.unified.store.get(original["id"]) == original


def test_validated_prior_agenda_only_and_raw_invalid_review_never_promoted(app, closed):
    from evograph.application.plan_repair_phase import validated_repair_agenda
    from test_saved_candidate_recheck import collect as recheck
    from test_saved_candidate_recheck import fake_provider as review_provider
    before, original = closed
    assert validated_repair_agenda(before, original) is None
    review_provider(app, outcome="held")
    recheck(app, before, original)
    held = app.unified.store.latest(before.id)
    agenda = validated_repair_agenda(before, held)
    assert agenda and agenda["plugins"] and agenda["candidate_id"] == original["id"]
    assert held["review_attempts"][0] == original["review_attempts"][0]
    calls = fake_provider(app, before, review="invalid")
    collect(app, before, held)
    assert len(calls) == 3
    assert '"repair_agenda"' in calls[0][1][0]["content"]
    assert agenda["run_id"] in calls[0][1][0]["content"]
    assert "new feedback remains authoritative" in calls[0][1][0]["content"]
    assert "only current review-task list" not in calls[0][1][0]["content"]
    assert any(message["role"] == "user" and FEEDBACK in message["content"] for message in calls[0][1])
    assert app.unified.store.get(original["id"]) == held


def test_native_composer_carries_same_pinned_new_phase(app, closed):
    import threading

    from evograph.transport.desktop import DesktopBridge
    before, original = closed
    calls = fake_provider(app, before, review="invalid")
    done, events = threading.Event(), []
    class Window:
        def evaluate_js(self, script):
            event = json.loads(script.split("{detail: ", 1)[1].rsplit("}))", 1)[0])["event"]
            events.append(event)
            if event["type"] == "done":
                done.set()
    bridge = DesktopBridge(app)
    bridge._window = Window()
    bridge.start_agent("offline-repair-composer", {"project_id": before.id, "content": FEEDBACK,
        "repair_from": app.projects.get(before.id)["plan_candidate"]["repair_from"], "snapshot_mode": "compact-v1"})
    assert done.wait(10)
    assert len(calls) == 3 and events[0]["turn_id"] != original["id"]
    assert not events[-1]["changed"]
    assert app.unified.store.latest(before.id)["repair_phase"]["pins"] == repair_phase_pins(before, original)
    assert app.unified.store.get(original["id"]) == original
