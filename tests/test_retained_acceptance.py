"""Offline guard checks. Scripted semantic opinions never prove reviewer quality."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from evograph.agent_tools.base import ToolContext
from evograph.application.plan_harness import replay_harness, run_harness, seal_snapshot
from evograph.application.plan_ir import PlanDelta, submit_plan_delta
from evograph.application.plan_review import (
    BATCH_VERSION,
    _expanded_evidence_catalog,
    _validate_materiality,
    batch_review_certificate,
    batch_review_packet,
    normalize_batch_review,
    resolve_packet_pointer,
)
from evograph.application.plan_stage import StagedDatabase
from evograph.application.plan_unit_schema import project_unit_schema
from evograph.application.plan_units import prepare_unit_request, validate_unit_delta
from evograph.application.retained_acceptance import (
    capture_entry,
    generation_context,
    projection,
    validate_metadata_scope,
    writable_ids,
)
from evograph.domain.models import Project, now, uid
from evograph.domain.plan_contracts import IntentSource, active_behaviors
from evograph.infrastructure.database import ConflictError
from test_architecture_unit_bootstrap import manifest_for, schedule
from test_plan_patch import initial, node, stage
from test_review_materiality import batch_for, context, material_issue

__all__ = ["context"]


def admitted(app, *, draft=False):
    ctx, first = stage(app)
    initial(ctx, milestones=[node(), node("LATER", dependencies=["CHECK"],
                                        dependency_reasons={"CHECK": "Uses checker output"})])
    canonical = first.project.model_copy(deep=True)
    canonical.revision = app.db.get(canonical.id).revision
    canonical.unified_planning = True
    app.db.save(canonical, "offline_fixture")
    ctx, db = stage(app, canonical, text="Keep accepted behavior. Simplify the helper implementation.")
    if not draft:
        return ctx, db, canonical
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "invention", "owner": "CHECK",
        "statement": "Unsupported draft-only helper choice", "requirement_ids": ["r"],
        "mechanism": "One draft helper"}]))
    previous = deepcopy(db.record)
    identity = uid()
    candidate = db.project.model_copy(deep=True)
    candidate.plan_contract.sources.append(IntentSource(id=identity, text="Remove the invented helper choice."))
    record = db.store.save({"id": identity, "turn_id": identity, "project_id": candidate.id,
        "created_at": now(), "base_revision": canonical.revision, "revision": candidate.revision,
        "status": "generating", "input": "Remove the invented helper choice.", "report": {}, "metrics": {},
        "project": candidate.model_dump(), "resumes_candidate_id": previous["id"]})
    db = StagedDatabase(app.db, db.store, record, identity)
    return ToolContext(candidate.id, SimpleNamespace(db=db)), db, canonical


def claim(db, key="check", origin="canonical_accepted"):
    return next(r for r in db.record["retained_acceptance"]["claims"] if r["key"] == key and r["origin"] == origin)


def change(db, **extra):
    return {"baseline_id": claim(db)["id"], "disposition": "replaced", "witness_keys": ["check"],
            "kind": "preserving_refactor", "reason": "Preserve the observable result with another helper", **extra}


def test_fixed_inventory_separates_canonical_and_all_entry_drafts(app):
    ctx, db, canonical = admitted(app, draft=True)
    entry = deepcopy(db.record["retained_acceptance"])
    assert [r["origin"] for r in entry["claims"]].count("canonical_accepted") == 2
    assert [r["origin"] for r in entry["claims"]].count("staged_draft") == 3
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "later_invention", "owner": "CHECK",
        "statement": "Intermediate invented detail", "requirement_ids": ["r"], "mechanism": "New helper"}]))
    assert db.record["retained_acceptance"] == entry
    assert not any(r["key"] == "later_invention" for r in entry["claims"])
    assert len(projection(canonical, db.project, db.record)["claims"]) == 5
    assert len(generation_context(db.record, db.project, {"invention"})["inventory"]) == 5


@pytest.mark.parametrize("field,value", [("statement", "Only inspect a weaker result"),
                                         ("mechanism", "Use a differently named ordinary helper")])
def test_wording_change_is_not_a_deterministic_veto_but_original_is_reviewed(app, field, value):
    ctx, db, canonical = admitted(app)
    original = active_behaviors(canonical)["check"].statement
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "check", field: value}]))
    projected = projection(canonical, db.project, db.record)
    row = next(r for r in projected["claims"] if r["id"] == claim(db)["id"])
    assert row["baseline_behavior"]["statement"] == original
    assert row["resolution"]["disposition"] == "retained"
    record = {**db.record, "input": "Keep accepted behavior", "review_protocol": BATCH_VERSION}
    packet = batch_review_packet(canonical, db.project, record)
    subject = "retained_acceptance:" + row["id"]
    assert packet["required_subjects"].count(subject) == 1
    batch = batch_for(packet)
    batch.statuses.supported.remove(subject)
    with pytest.raises(ValueError, match="精确覆盖"):
        normalize_batch_review(db.project, packet, batch)


@pytest.mark.parametrize("mutation", ["missing", "hash", "drop_claim", "rewrite_binding"])
def test_transaction_rejects_missing_stale_or_forged_entry(app, mutation):
    _, db, _ = admitted(app)
    before = deepcopy(db.record)
    bad = deepcopy(before)
    if mutation == "missing":
        del bad["retained_acceptance"]
    elif mutation == "hash":
        bad["retained_acceptance"]["pins"]["canonical_hash"] = "stale"
    elif mutation == "drop_claim":
        bad["retained_acceptance"]["claims"].pop()
    else:
        bad["retained_acceptance"]["claims"][0]["binding"]["mechanism"] = "weakened"
    with pytest.raises((ValueError, ConflictError)):
        db.store.save(bad)
    assert db.store.get(before["id"]) == before


@pytest.mark.parametrize("key", ["forged", "later"])
def test_baseline_ids_are_not_writable_via_witness_or_unrelated_holder(app, key):
    _, db, _ = admitted(app)
    raw = {"contracts": [{"key": key, "acceptance_changes": [change(db)]}]}
    with pytest.raises(ValueError, match="outside assigned holder"):
        validate_metadata_scope(db.record, db.project, raw)
    raw["contracts"][0]["key"] = "check"
    raw["contracts"][0]["acceptance_changes"][0]["baseline_id"] = "invented"
    with pytest.raises(ValueError, match="stale"):
        validate_metadata_scope(db.record, db.project, raw)


@pytest.mark.parametrize("witness", ["later", "future-unit", "stale-revision-id"])
def test_later_missing_and_stale_witnesses_cannot_cover_current_acceptance(app, witness):
    ctx, db, _ = admitted(app)
    before = deepcopy(db.record)
    with pytest.raises(ValueError, match="retained acceptance"):
        submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "check", "statement": "Refactored acceptance",
            "acceptance_changes": [change(db, witness_keys=[witness])]}]))
    assert db.record == before


def test_legitimate_earlier_extraction_and_holder_authority(app):
    ctx, db, canonical = admitted(app)
    # CHECK is an actual predecessor of LATER, so both witnesses exist at LATER.
    item = {**change(db), "baseline_id": claim(db, "later")["id"], "witness_keys": ["check", "later"]}
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "later", "mechanism": "Uses shared earlier checker",
                                                "acceptance_changes": [item]}]))
    projection(canonical, db.project, db.record)
    assert claim(db, "later")["id"] in writable_ids(db.record, db.project, {"check"})
    # Merely being another witness does not grant ownership of CHECK's claim.
    assert claim(db)["id"] not in writable_ids(db.record, db.project, {"later"})


def test_delete_without_disposition_and_slice_swallow_are_rejected(app):
    ctx, db, _ = admitted(app)
    with pytest.raises(ValueError, match="retained acceptance active witness"):
        submit_plan_delta(ctx, PlanDelta(remove_contract_keys=["later"], remove_slice_ids=["LATER"]))
    with pytest.raises(ValueError, match="explicit contained contract"):
        submit_plan_delta(ctx, PlanDelta(remove_slice_ids=["LATER"]))
    with pytest.raises(ValueError, match="explicit remove_contract_keys"):
        validate_metadata_scope(db.record, db.project, {"removal_acceptance_changes": {"check": [change(db)]}})


def test_explicit_withdrawal_keeps_history_and_is_not_semantic_authorization(app):
    ctx, db, canonical = admitted(app)
    source = db.source_id
    withdrawal = change(db, baseline_id=claim(db, "later")["id"], disposition="retired", witness_keys=[],
        kind="user_scope_change", source_id=source, quote="Simplify the helper implementation.",
        reason="Generator alleges that this permits scope loss")
    original = active_behaviors(db.project)["later"].id
    submit_plan_delta(ctx, PlanDelta(remove_contract_keys=["later"], remove_slice_ids=["LATER"],
                                    removal_acceptance_changes={"later": [withdrawal]}))
    row = next(r for r in projection(canonical, db.project, db.record)["claims"] if r["id"] == withdrawal["baseline_id"])
    assert row["resolution"]["disposition"] == "retired"
    assert any(b.id == original for b in db.project.behaviors)
    record = {**db.record, "input": "Simplify the helper implementation.", "review_protocol": BATCH_VERSION}
    packet = batch_review_packet(canonical, db.project, record)
    subject = "retained_acceptance:" + row["id"]
    index = next(i for i, r in enumerate(packet["retained_acceptance"]["claims"]) if r["id"] == row["id"])
    issue = material_issue([subject], "contradicted")
    issue["materiality"].update(obligation_ref=f"/retained_acceptance/claims/{index}/baseline_behavior/statement",
        obligation_excerpt="Observable later result", affected_owner_ids=[], boundary_refs=["/current_input"],
        necessary_plan_change="Retain acceptance; simplifying a helper did not withdraw the result")
    issue["evidence_refs"] = [f"/retained_acceptance/claims/{index}", "/current_input"]
    review = normalize_batch_review(db.project, packet, batch_for(packet, issues=[issue]))
    assert next(c for c in review.checks if c.subject == subject).verdict == "contradicted"


def test_draft_invention_correction_is_supported_without_canonical_relabel(app):
    ctx, db, canonical = admitted(app, draft=True)
    item = change(db, baseline_id=claim(db, "invention", "staged_draft")["id"], disposition="retired", witness_keys=[],
                  kind="draft_correction", source_id=db.source_id, quote="Remove the invented helper choice.")
    submit_plan_delta(ctx, PlanDelta(remove_contract_keys=["invention"], removal_acceptance_changes={"invention": [item]}))
    projection(canonical, db.project, db.record)
    with pytest.raises(ValueError, match="canonical claim"):
        submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "check", "mechanism": "Changed helper",
            "acceptance_changes": [change(db, kind="draft_correction", source_id=db.source_id,
                                          quote="Remove the invented helper choice.")]}]))


def test_unit_schema_and_runtime_bind_metadata_to_assigned_holder(app):
    ctx, db, _ = admitted(app)
    db.segmented_planning = True
    raw = {"contracts": [{"key": "check", "mechanism": "New helper", "acceptance_changes": [change(db)]}]}
    schedule(ctx, manifest_for(raw))
    prepare_unit_request(db)
    schema, audit = project_unit_schema(db.record, db.project)
    assert audit["status"] == "projected"
    row = schema["properties"]["contracts"]["items"]
    assert row["properties"]["key"]["enum"] == ["check"]
    assert row["properties"]["acceptance_changes"]["items"]["properties"]["baseline_id"]["enum"] == [claim(db)["id"]]
    forged = deepcopy(raw)
    forged["contracts"][0]["acceptance_changes"][0]["baseline_id"] = claim(db, "later")["id"]
    with pytest.raises(ValueError, match="outside assigned holder"):
        validate_unit_delta(db, forged)
    submit_plan_delta(ctx, PlanDelta.model_validate(raw))
    assert db.record["work_units"]["completed_ids"]
    saved = deepcopy(db.record)
    assert submit_plan_delta(ctx, PlanDelta.model_validate(raw))["status"] == "NO_PROGRESS"
    assert db.record["project"] == saved["project"]


def test_exact_sealed_replay_and_alias_hash_type_cycles(context):
    project, record, _, _ = context
    record = {**record, "review_protocol": BATCH_VERSION,
              "retained_acceptance": capture_entry(project)}
    snapshot = seal_snapshot(project, project, record)
    packet = batch_review_packet(project, project, record)
    batch = batch_for(packet)
    review = normalize_batch_review(project, packet, batch)
    certificate = batch_review_certificate(json.dumps(batch.model_dump()), batch)
    async def evaluate(_):
        return review, certificate
    run = asyncio.run(run_harness(snapshot, evaluate))
    assert run.decision == "apply"
    assert replay_harness(snapshot, run, lambda *_: review).decision == "apply"
    changed = deepcopy(record)
    changed["retained_acceptance"]["claims"].pop()
    with pytest.raises(ValueError):
        replay_harness(seal_snapshot(project, project, changed), run, lambda *_: review)
    pointer = "/retained_acceptance/claims/0/baseline_behavior"
    assert resolve_packet_pointer(packet, pointer)["id"] == "B1"
    for target in (pointer, "/candidate/plan_contract/bindings/0", "/candidate/behaviors/1"):
        bad = deepcopy(packet)
        bad["retained_acceptance"]["claims"][0]["baseline_behavior"]["$packet_ref"] = target
        with pytest.raises(ValueError):
            resolve_packet_pointer(bad, pointer)


def test_compiler_history_mutation_and_legacy_resume_fail_closed(app):
    ctx, db, canonical = admitted(app)
    submit_plan_delta(ctx, PlanDelta(contracts=[{"key": "check", "mechanism": "Use another helper",
                                                "acceptance_changes": [change(db)]}]))
    for audits in ([], [{"ir": {"contracts": []}}]):
        bad = {**db.record, "compilations": audits}
        with pytest.raises((ValueError, ConflictError), match="compiler history"):
            db.store.save(bad)
    with pytest.raises(ValueError, match="legacy entry"):
        projection(canonical, db.project, {"resumes_candidate_id": "missing"})


def test_retained_mechanism_materiality_needs_statement_or_source(context):
    project, record, _, _ = context
    packet = batch_review_packet(project, project, record)
    issue = material_issue(["retained_acceptance:canonical:B1"])
    issue["materiality"].update(obligation_ref="/retained_acceptance/claims/0/baseline_binding/mechanism",
                                obligation_excerpt="Within one transaction")
    from evograph.application.plan_review import BatchIssue
    with pytest.raises(ValueError, match="original statement"):
        _validate_materiality(project, packet, BatchIssue.model_validate(issue), set(_expanded_evidence_catalog(packet)))
    issue["materiality"]["boundary_refs"] = ["/retained_acceptance/claims/0/baseline_behavior/statement"]
    _validate_materiality(project, packet, BatchIssue.model_validate(issue), set(_expanded_evidence_catalog(packet)))


def test_real_removal_direction_is_structurally_possible_with_explicit_source(app):
    ctx, db, canonical = admitted(app)
    identity = uid()
    candidate = db.project.model_copy(deep=True)
    candidate.plan_contract.sources.append(IntentSource(id=identity, text="Remove the later result."))
    db.record = db.store.save({**db.record, "project": candidate.model_dump()})
    db.project = candidate
    item = change(db, baseline_id=claim(db, "later")["id"], disposition="retired", witness_keys=[],
                  kind="user_scope_change", source_id=identity, quote="Remove the later result.")
    submit_plan_delta(ctx, PlanDelta(remove_contract_keys=["later"], remove_slice_ids=["LATER"],
                                    removal_acceptance_changes={"later": [item]}))
    projected = projection(canonical, db.project, db.record)
    row = next(r for r in projected["claims"] if r["id"] == item["baseline_id"])
    assert row["resolution"]["source_id"] == identity and row["witnesses"] == []
    assert len(projected["claims"]) == 2  # The retired claim is still auditable.


def test_modeled_declaration_deletion_never_turns_into_coverage(context):
    project, _, _, _ = context
    before = project.model_copy(deep=True)
    binding = before.plan_contract.bindings[0]
    from evograph.domain.plan_contracts import InvokeCapabilityStep, ProvidedCapability
    binding.provides = [ProvidedCapability(key="book", kind="command", action="book", consumes=[])]
    # Typed shape only: independent typed plugin still owns action semantics.
    binding.steps = [InvokeCapabilityStep(kind="invoke_command", capability_key="book", quote="book")]
    before = Project.model_validate(before.model_dump())
    entry = capture_entry(before)
    candidate = before.model_copy(deep=True)
    candidate.plan_contract.bindings[0].provides = []
    candidate.plan_contract.bindings[0].steps = None
    with pytest.raises(ValueError, match="typed declaration lost"):
        projection(before, candidate, {"retained_acceptance": entry})
    candidate.plan_contract.bindings[0].steps = before.plan_contract.bindings[0].steps
    with pytest.raises(ValueError, match="modeled consumption lost"):
        projection(before, candidate, {"retained_acceptance": entry})
    untouched = projection(project, project, {"retained_acceptance": capture_entry(project)})
    assert all(row["baseline_binding"]["steps"] is None for row in untouched["claims"])


def test_removal_metadata_is_narrowly_projected_and_null_is_rejected(app):
    ctx, db, _ = admitted(app)
    db.segmented_planning = True
    raw = {"remove_contract_keys": ["later"], "remove_slice_ids": ["LATER"]}
    schedule(ctx, manifest_for(raw))
    prepare_unit_request(db)
    schema, audit = project_unit_schema(db.record, db.project)
    assert audit["status"] == "projected"
    mapping = schema["properties"]["removal_acceptance_changes"]
    assert set(mapping["properties"]) == {"later"} and mapping["additionalProperties"] is False
    ids = mapping["properties"]["later"]["items"]["properties"]["baseline_id"]["enum"]
    assert ids == [claim(db, "later")["id"]]
    for malformed in ({"contracts": [{"key": "check", "acceptance_changes": None}]},
                      {"removal_acceptance_changes": {"later": [change(db, reason=None)]}}):
        with pytest.raises(ValueError):
            PlanDelta.model_validate(malformed)


def test_stale_witness_binding_and_deleted_original_history_fail(context):
    project, record, _, _ = context
    entry = capture_entry(project)
    candidate = project.model_copy(deep=True)
    candidate.plan_contract.bindings[0].behavior_revision_id = "B0-obsolete"
    with pytest.raises(ValueError, match="witness missing or stale"):
        projection(project, candidate, {**record, "retained_acceptance": entry})
    candidate = project.model_copy(deep=True)
    candidate.behaviors.pop(0)
    with pytest.raises(ValueError, match="original behavior history"):
        projection(project, candidate, {**record, "retained_acceptance": entry})


def test_staged_source_history_cannot_be_deleted_or_rewritten(app):
    _, db, canonical = admitted(app, draft=True)
    entry_sources = set(db.record["retained_acceptance"]["history_pins"])
    source = next(s for s in db.project.plan_contract.sources
                  if "source:" + s.id in entry_sources and s.id not in {x.id for x in canonical.plan_contract.sources})
    for action in ("remove", "rewrite"):
        candidate = db.project.model_copy(deep=True)
        if action == "remove":
            candidate.plan_contract.sources = [s for s in candidate.plan_contract.sources if s.id != source.id]
        else:
            next(s for s in candidate.plan_contract.sources if s.id == source.id).text = "Forged approval"
        with pytest.raises(ValueError, match="source/requirement history"):
            projection(canonical, candidate, db.record)
        with pytest.raises(ValueError, match="source/requirement history"):
            db.store.save({**db.record, "project": candidate.model_dump()})
