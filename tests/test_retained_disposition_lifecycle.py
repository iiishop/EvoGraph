"""Audit-only units and exact predecessor continuity never imply semantic approval."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.application.plan_harness import replay_harness, run_deterministic, seal_snapshot
from evograph.application.plan_ir import PlanDelta, submit_plan_delta
from evograph.application.plan_review import (
    CHECKER_VERSION,
    SCOPED_BATCH_VERSION,
    batch_review_certificate,
    batch_review_packet,
    normalize_batch_review,
    validate_batch_certificate,
)
from evograph.application.plan_stage import StagedDatabase
from evograph.application.plan_unit_schema import project_unit_schema
from evograph.application.plan_units import (
    all_units_complete,
    prepare_unit_request,
    schedule_findings,
)
from evograph.application.retained_acceptance import projection
from evograph.domain.models import now, uid
from evograph.domain.plan_contracts import IntentSource, active_behaviors, candidate_hash
from test_architecture_unit_bootstrap import manifest_for, schedule
from test_bounded_planning_job import seven
from test_plan_patch import node
from test_retained_acceptance import admitted, change, claim
from test_review_materiality import batch_for

__all__ = ["seven"]


def source(db, text):
    identity = uid()
    project = db.project.model_copy(deep=True)
    project.plan_contract.sources.append(IntentSource(id=identity, text=text))
    db.record = db.store.save({**db.record, "project": project.model_dump()})
    db.project = project
    return identity


def next_request(app, db, canonical, text):
    old = deepcopy(db.record)
    identity = uid()
    project = db.project.model_copy(deep=True)
    project.plan_contract.sources.append(IntentSource(id=identity, text=text))
    record = db.store.save({"id": identity, "turn_id": identity, "project_id": project.id,
        "created_at": now(), "base_revision": canonical.revision, "revision": project.revision,
        "status": "generating", "input": text, "report": {}, "metrics": {},
        "project": project.model_dump(), "resumes_candidate_id": old["id"]})
    assert db.store.get(old["id"]) == old
    new = StagedDatabase(app.db, db.store, record, identity)
    return ToolContext(project.id, SimpleNamespace(db=new)), new


def assign(ctx, db, raw, *, fields=None):
    db.segmented_planning = True
    manifest = manifest_for(raw)
    if fields:
        manifest[0]["fields"] = fields
    schedule(ctx, manifest)
    prepare_unit_request(db)


def test_explicit_disposition_only_unit_changes_audit_not_plan_and_invalidates_scope(app):
    ctx, db, canonical = admitted(app)
    raw = {"contracts": [{"key": "check", "acceptance_changes": [change(db)]}]}
    assign(ctx, db, raw)
    db.record.update(input="Keep behavior", review_protocol=SCOPED_BATCH_VERSION, checker_version=CHECKER_VERSION)
    db.record = db.store.save(db.record)
    prior = deepcopy(db.record)
    snapshot = seal_snapshot(canonical, db.project, prior)
    run = run_deterministic(snapshot)
    packet = batch_review_packet(canonical, db.project, prior)
    from evograph.application.unified_planning import retained_disposition_changed
    assert not retained_disposition_changed(canonical, db.project, prior, packet)
    batch = batch_for(packet)
    old_review = normalize_batch_review(db.project, packet, batch)
    old_certificate = batch_review_certificate(json.dumps(batch.model_dump()), batch)
    before = db.project.model_dump()
    result = submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert result["status"] == "ACCEPTANCE_UPDATED"
    assert db.project.model_dump() == before
    assert candidate_hash(db.project) == prior["candidate_hash"]
    assert db.record["retained_acceptance"] == prior["retained_acceptance"]
    checkpoint = db.record["work_units"]["checkpoints"][-1]
    assert checkpoint["acceptance_only"] is True and checkpoint["changed"] is False
    assert checkpoint["before_revision"] == checkpoint["after_revision"]
    assert all_units_complete(db.record) and not schedule_findings(db.record, db.project)
    assert db.record["validation_requested"] is False
    assert retained_disposition_changed(canonical, db.project, db.record, packet)
    assert db.record["validation_receipt"]["model"]["status"] == "not_run"
    assert batch_review_packet(canonical, db.project, db.record)["review_scope_hash"] != packet["review_scope_hash"]
    with pytest.raises(ValueError, match="评审依据"):
        validate_batch_certificate(canonical, db.project, {**db.record, "review_inputs": [packet],
            "batch_reviews": [old_certificate], "reviews": [old_review.model_dump()],
            "report": {"semantic_batch": batch.model_dump(), "semantic": old_review.model_dump()}})
    with pytest.raises(ValueError, match="changed"):
        replay_harness(seal_snapshot(canonical, db.project, db.record), run, lambda *_: None)
    saved = deepcopy(db.record)
    with pytest.raises(ValueError, match="already completed"):
        submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert db.record == saved


@pytest.mark.parametrize("fields", [["statement"], ["mechanism"], ["statement", "acceptance_changes"]])
def test_metadata_cannot_complete_substantive_unit(app, fields):
    ctx, db, _ = admitted(app)
    raw = {"contracts": [{"key": "check", "acceptance_changes": [change(db)]}]}
    assign(ctx, db, raw, fields=fields)
    old = deepcopy(db.record)
    with pytest.raises(ValueError, match="NO_PROGRESS"):
        submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert db.store.get(old["id"]) == db.record == old


def retire(app):
    ctx, db, canonical = admitted(app)
    sid = source(db, "Remove the later result.")
    item = change(db, baseline_id=claim(db, "later")["id"], disposition="retired", witness_keys=[],
                  kind="user_scope_change", source_id=sid, quote="Remove the later result.")
    submit_plan_delta(ctx, PlanDelta(remove_contract_keys=["later"], remove_slice_ids=["LATER"],
                                    removal_acceptance_changes={"later": [item]}))
    return db, canonical, item


def test_retirement_survives_new_request_reaffirmation_and_explicit_restoration(app):
    old, canonical, item = retire(app)
    ctx, db = next_request(app, old, canonical, "Keep the removal and clarify remaining behavior.")
    identity = item["baseline_id"]
    rows = projection(canonical, db.project, db.record)["claims"]
    prior = next(row for row in rows if row["id"] == identity)
    assert prior["resolution"]["disposition"] == "retired"
    assert prior["resolution"]["inherited_proposal"] is True
    assert prior["resolution"]["source_id"] == item["source_id"]
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "check", "mechanism": "Different ordinary helper"}]))
    reaffirm = {**item, "source_id": db.source_id, "quote": "Keep the removal", "reason": "Explicit new confirmation"}
    raw = {"contracts": [{"key": "later", "acceptance_changes": [reaffirm]}]}
    assign(ctx, db, raw)
    schema, audit = project_unit_schema(db.record, db.project)
    assert audit["status"] == "projected"
    assert schema["properties"]["contracts"]["items"]["required"] == ["key", "acceptance_changes"]
    before = db.project.model_dump()
    assert submit_plan_delta(ctx, PlanDelta.model_validate(raw))["status"] == "ACCEPTANCE_UPDATED"
    assert db.project.model_dump() == before and "later" not in active_behaviors(db.project)
    # A genuinely new direction may explicitly restore the inactive contract.
    ctx, restored = next_request(app, db, canonical, "Restore the later result.")
    restoration = {**item, "disposition": "replaced", "witness_keys": ["later"],
                   "source_id": restored.source_id, "quote": "Restore the later result.", "reason": "Explicit restoration"}
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "later", "owner": "LATER",
        "statement": "Observable later result", "mechanism": "Restore later output", "requirement_ids": ["r"],
        "acceptance_changes": [restoration]}], restore_contract_keys=["later"], slices=[
        {k: v for k, v in node("LATER").items() if k != "behaviors"}]))
    assert "later" in active_behaviors(restored.project)
    assert next(r for r in projection(canonical, restored.project, restored.record)["claims"]
                if r["id"] == identity)["resolution"]["disposition"] == "replaced"


def test_identical_inherited_disposition_and_unrelated_source_forgery_do_not_progress(app):
    old, canonical, item = retire(app)
    ctx, db = next_request(app, old, canonical, "Clarify another helper.")
    raw = {"contracts": [{"key": "later", "acceptance_changes": [item]}]}
    assign(ctx, db, raw)
    prior = deepcopy(db.record)
    with pytest.raises(ValueError, match="unchanged disposition"):
        submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    forged = deepcopy(raw)
    forged["contracts"][0]["acceptance_changes"][0].update(source_id=db.source_id, quote="Remove the later result.")
    with pytest.raises(ValueError, match="exact source provenance"):
        submit_plan_delta(ctx, PlanDelta.model_validate(forged))
    assert db.record == db.store.get(prior["id"]) == prior
    assert "inherited_canonical_dispositions" in db.record["retained_acceptance"]
    assert "semantic" not in db.record["report"]  # Continuity is a proposal, not approval.


def test_renamed_holder_survives_new_request_without_restoring_old_identity(app):
    ctx, db, canonical = admitted(app)
    item = change(db, baseline_id=claim(db, "later")["id"], witness_keys=["replacement"])
    submit_plan_delta(ctx, PlanDelta(remove_contract_keys=["later"],
        removal_acceptance_changes={"later": [item]}, contracts=[{"key": "replacement", "owner": "LATER",
        "statement": "Observable later result", "mechanism": "Renamed implementation", "requirement_ids": ["r"]}]))
    ctx, new = next_request(app, db, canonical, "Clarify the new holder helper.")
    row = next(r for r in projection(canonical, new.project, new.record)["claims"] if r["id"] == item["baseline_id"])
    assert row["resolution"]["holder"] == "replacement"
    assert row["resolution"]["witness_keys"] == ["replacement"]
    assert row["resolution"]["inherited_proposal"] is True
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "replacement", "mechanism": "Still preserves output"}]))
    assert "later" not in active_behaviors(new.project)


def test_disposition_checkpoint_replays_against_durable_unit_inside_writer_lock(app):
    from evograph.application.plan_ir import compile_plan_delta
    from evograph.application.plan_patch import _completed_compiler_audit
    from evograph.infrastructure.database import ConflictError
    ctx, db, _ = admitted(app)
    raw = {"contracts": [{"key": "check", "acceptance_changes": [change(db)]}]}
    assign(ctx, db, raw)
    old = deepcopy(db.record)
    compiled = compile_plan_delta(db.project, raw, retained_record=old)
    audit = _completed_compiler_audit(compiled.audit, db.project, db.project, changed=False)
    # An otherwise valid audit cannot bypass the matching assigned checkpoint.
    forged = {**old, "compilations": [*old.get("compilations", []), audit]}
    with pytest.raises(ConflictError, match="unit checkpoint changed"):
        db.store.save(forged)
    assert db.store.get(old["id"]) == old
    assert submit_plan_delta(ctx, PlanDelta.model_validate(raw))["status"] == "ACCEPTANCE_UPDATED"


def test_disposition_only_checkpoint_survives_real_same_job_phase_boundary(app, seven):
    from test_agent_stream import tool_chunks
    from test_bounded_planning_job import collect, provider, request
    normal_calls = provider(app, seven)
    ordinary_stream = app.settings.stream
    seen = []
    async def stream(messages, schemas, **kwargs):
        names = {s["function"]["name"] for s in schemas}
        record = app.unified.store.latest(seven.id)
        if "schedule_plan_changes" in names:
            raw = {"changes": [{"kind": "contract", "id": f"check{i}",
                "fields": ["acceptance_changes"] if i == 0 else ["statement"], "uses": [],
                "intent": "Record explicit preserved disposition" if i == 0 else "Clarify statement"} for i in range(7)]}
            async for event in tool_chunks("schedule_plan_changes", raw):
                yield event
            yield {"type": "usage", "tokens": 7}
        elif "submit_plan_delta" in names and record["unit_request"]["unit_id"] == "unit-001":
            identity = next(row["id"] for row in record["retained_acceptance"]["claims"] if row["key"] == "check0")
            raw = {"contracts": [{"key": "check0", "acceptance_changes": [{
                "baseline_id": identity, "disposition": "retained", "witness_keys": ["check0"],
                "kind": "preserving_refactor", "reason": "Explicitly retain original behavior at its original stage"}]}]}
            seen.append(record["project"])
            async for event in tool_chunks("submit_plan_delta", raw):
                yield event
            yield {"type": "usage", "tokens": 7}
        else:
            async for event in ordinary_stream(messages, schemas, **kwargs):
                yield event
    app.settings.stream = stream
    events = collect(app, seven, request(app, seven))
    job = app.planning_jobs.store.get("offline-job")
    assert job["status"] == "applied", [e for e in events if e["type"] in {"error", "tool_failed"}]
    assert len(job["phases"]) == 2 and len(normal_calls) == 7
    first, second = [app.unified.store.get(p["candidate_id"]) for p in job["phases"]]
    assert first["retained_acceptance"] == second["retained_acceptance"]
    checkpoint = first["work_units"]["checkpoints"][0]
    assert checkpoint["acceptance_only"] and checkpoint["before_revision"] == checkpoint["after_revision"]
    assert second["work_units"]["checkpoints"][:len(first["work_units"]["checkpoints"])] == first["work_units"]["checkpoints"]
    assert all_units_complete(second)
    assert second["harness_commit_replay"]["run"]["decision"] == "apply"


def test_new_entry_inherited_proposal_is_server_derived_not_model_supplied(app):
    from evograph.infrastructure.database import ConflictError
    old, canonical, _ = retire(app)
    _, db = next_request(app, old, canonical, "Clarify another helper.")
    saved = deepcopy(db.record)
    forged = deepcopy(saved)
    seed = next(iter(forged["retained_acceptance"]["inherited_canonical_dispositions"].values()))
    seed["source_id"] = db.source_id
    seed["quote"] = "Clarify another helper."
    with pytest.raises(ConflictError, match="fixed entry"):
        db.store.save(forged)
    assert db.store.get(saved["id"]) == saved
