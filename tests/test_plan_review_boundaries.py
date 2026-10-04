"""Deterministic source-completeness and change-boundary checks, not semantic proof."""
import base64
import json
from copy import deepcopy
from pathlib import Path

import pytest
from evograph.application.unified_planning import review_packet, validate_attachment_excerpts
from evograph.domain.models import Attachment, Project
from evograph.domain.plan_contracts import IntentSource, active_behaviors, candidate_hash
from test_unified_planning import collect, enable, provider


@pytest.mark.parametrize("length", [60000, 60001])
def test_clipped_attachment_is_retained_before_any_provider_call(app, length):
    p = enable(app)
    data = ("x" * length + "\nNever access the network.").encode()
    attachment = app.attachments.upload(p.id, "requirements.txt", base64.b64encode(data).decode())
    before = app.db.get(p.id)
    calls = []

    async def stream(*args):
        calls.append(args)
        yield {"type": "text", "text": "Should not be invoked"}

    app.settings.stream = stream
    events = collect(app, before, "按照附件生成方案", attachment_ids=[attachment.id])
    record = app.unified.store.latest(p.id)
    assert calls == []
    assert record["status"] == "failed"
    assert record["metrics"]["provider_calls"] == 0
    assert "裁剪边界" in record["report"]["findings"][-1]["message"]
    assert any(event["type"] == "candidate_changed" for event in events)
    assert app.db.get(p.id) == before
    source = record["project"]["plan_contract"]["sources"][-1]
    assert source["message_id"]
    assert source["reference_context"]["attachments"][0]["excerpt_limit_reached"] is True
    assert app.db.messages(p.id)[-1]["content"] == "按照附件生成方案"


def test_historical_attachment_without_flag_cannot_be_treated_as_complete(app):
    p = enable(app)
    p.plan_contract.sources.append(IntentSource(id="old", text="Use this source", reference_context={
        "attachments": [{"id": "a", "name": "old.txt", "text": "x" * 60000}],
    }))
    with pytest.raises(ValueError, match="裁剪边界"):
        review_packet(p, p, {"id": "review", "base_revision": p.revision, "input": "review"})


def test_short_excerpt_and_explicit_uncertainty_are_distinct(app):
    p = enable(app)
    p.plan_contract.sources.append(IntentSource(id="s", text="source", reference_context={
        "attachments": [{"id": "a", "text": "x" * 59999}],
    }))
    validate_attachment_excerpts(p)
    p.plan_contract.sources[-1].reference_context["attachments"][0]["excerpt_limit_reached"] = True
    with pytest.raises(ValueError, match="裁剪边界"):
        validate_attachment_excerpts(p)


def test_unselected_but_read_attachment_at_clip_boundary_blocks_review(app):
    p = enable(app)
    p.attachments.append(Attachment(id="a", name="read.txt", media_type="text/plain",
                                    size=60010, excerpt="x" * 60000))
    p.plan_contract.sources.append(IntentSource(id="s", text="Read project files", evidence=[{
        "name": "read_reference", "arguments": {"attachment_id": "a", "offset": 0},
        "result": {"text": "x" * 12000, "has_more": True},
    }]))
    with pytest.raises(ValueError, match="裁剪边界"):
        review_packet(p, p, {"id": "review", "base_revision": p.revision, "input": "review"})


def test_review_diff_is_computed_from_exact_projects_not_supplied_audit():
    raw = json.loads((Path(__file__).parent / "fixtures/qa49_frozen_plan.json").read_text())["project"]
    before = Project.model_validate(raw)
    before.revision = 2  # Canonical initial run R2; fixture's planning payload is identical.
    after = before.model_copy(deep=True)
    after.revision = 3
    behavior = active_behaviors(after)["format-two-decimals"]
    old_statement = behavior.statement
    behavior.statement += " 批量输出每个值各占一行。"
    record = {"id": "candidate", "base_revision": 2, "input": "Change batch behavior",
              "compilations": [{"contract_changes": [], "candidate_hash": "forged"}]}
    packet = json.loads(review_packet(before, after, record))
    delta = packet["contract_delta"]
    assert delta["base_revision"] == 2 and delta["candidate_revision"] == 3
    assert delta["base_candidate_hash"] == candidate_hash(before)
    assert delta["candidate_hash"] == packet["candidate_hash"] == candidate_hash(after)
    assert len(delta["changes"]) == 1
    change = delta["changes"][0]
    assert change["key"] == "format-two-decimals"
    assert change["before_owner"] == change["after_owner"] == behavior.owner
    assert change["fields"]["statement"] == {"before": old_statement, "after": behavior.statement}
    frozen = deepcopy(delta)
    behavior.statement += " Another added promise."
    changed = json.loads(review_packet(before, after, record))["contract_delta"]
    assert changed["candidate_hash"] != frozen["candidate_hash"]
    assert changed["changes"] != frozen["changes"]


def test_review_diff_noop_does_not_borrow_historical_changes(app):
    before = enable(app)
    record = {"id": "candidate", "base_revision": before.revision, "input": "Explain only",
              "compilations": [{"contract_changes": [{"key": "fake"}]}]}
    assert json.loads(review_packet(before, before, record))["contract_delta"]["changes"] == []


def test_cross_window_discard_finishes_receipt_without_reanimating_candidate(app):
    from evograph.infrastructure.database import Database
    from evograph.infrastructure.plan_candidates import CandidateStore
    p = enable(app)
    other = CandidateStore(Database(app.db.path))
    discarded = []

    def discard_during_review():
        record = other.latest(p.id)
        other.discard(p.id, record["id"])
        discarded.append(record["id"])

    provider(app, on_review=discard_during_review)
    events = collect(app, p)
    assert app.db.get(p.id) == p
    saved = other.get(discarded[0])
    assert saved["status"] == "discarded"
    assert app.unified.store.latest(p.id) is None
    assert events[-1]["type"] == "done"
    assert not events[-1]["changed"]
    summary = events[-1]["summary"]
    assert summary["status"] == "stopped"
    assert summary["candidate_outcome"]["status"] == "discarded"
    assert "已放弃" in summary["candidate_outcome"]["note"]
    receipts = [e for e in app.db.events(p.id) if e["kind"] == "agent_turn_finished"]
    assert len(receipts) == 1
    assert json.loads(receipts[0]["detail"]) == summary
    audits = [json.loads(e["detail"]) for e in app.db.events(p.id)
              if e["kind"] == "candidate_terminal_audit"]
    assert len(audits) == 1
    assert audits[0]["terminal_status"] == "discarded"
    assert audits[0]["partial_observations"] is True
    assert audits[0]["terminal_candidate_hash"] == saved["candidate_hash"]
    assert audits[0]["metrics"]["calls"][-1]["response_audit"]["tool_calls"][0]["name"] == "submit_plan_review"
    assert audits[0]["metrics"]["tokens"] == 18
    assert "model_inputs" not in audits[0]  # Already durably saved before dispatch.
    assert "project" not in audits[0]
    assert p.id not in app.agent.active_turns


def test_commit_ack_failure_recovers_existing_applied_receipt(app, monkeypatch):
    p = enable(app)
    provider(app)
    real_commit = app.unified.store.commit

    def failed_ack(*args):
        real_commit(*args)
        raise OSError("Simulated lost acknowledgement after successful transaction")

    monkeypatch.setattr(app.unified.store, "commit", failed_ack)
    events = collect(app, p)
    saved = app.unified.store.latest(p.id)
    assert saved["status"] == "applied"
    assert events[-1]["type"] == "done" and events[-1]["changed"]
    assert events[-1]["summary"] == saved["turn_summary"]
    assert events[-1]["summary"]["status"] == "completed"
    assert len([e for e in app.db.events(p.id) if e["kind"] == "agent_turn_finished"]) == 1


def test_noop_ack_failure_remains_unchanged_and_reuses_existing_receipt(app, monkeypatch):
    p = enable(app)

    async def stream(*args):
        yield {"type": "text", "text": "仅解释，不修改"}

    app.settings.stream = stream
    real_noop = app.unified.store.noop

    def failed_ack(*args):
        real_noop(*args)
        raise OSError("Lost noop acknowledgement")

    monkeypatch.setattr(app.unified.store, "noop", failed_ack)
    events = collect(app, p)
    saved = app.unified.store.latest(p.id)
    assert saved["status"] == "applied" and saved["applied_revision"] == saved["base_revision"]
    assert app.db.get(p.id) == p
    assert not events[-1]["changed"]
    assert events[-1]["summary"] == saved["turn_summary"]
    assert events[-2]["label"] == "本轮未修改规划"
    assert len([e for e in app.db.events(p.id) if e["kind"] == "agent_turn_finished"]) == 1


def test_late_audit_write_failure_is_reported_without_losing_terminal_receipt(app, monkeypatch):
    p = enable(app)
    discarded = []

    def discard_during_review():
        record = app.unified.store.latest(p.id)
        discarded.append(app.unified.store.discard(p.id, record["id"]))

    provider(app, on_review=discard_during_review)
    real_event = app.unified.store.event

    def fail_audit_only(project_id, kind, detail):
        if kind == "candidate_terminal_audit":
            raise OSError("Simulated audit storage failure")
        return real_event(project_id, kind, detail)

    monkeypatch.setattr(app.unified.store, "event", fail_audit_only)
    events = collect(app, p)
    assert any(event.get("code") == "terminal_audit_not_saved" for event in events)
    assert app.unified.store.get(discarded[0]["id"]) == discarded[0]
    assert events[-1]["type"] == "done" and not events[-1]["changed"]
    assert len([e for e in app.db.events(p.id) if e["kind"] == "agent_turn_finished"]) == 1
    assert p.id not in app.agent.active_turns
