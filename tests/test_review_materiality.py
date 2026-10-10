"""Offline admission/replay checks; they do not measure model judgment quality."""
import asyncio
import json
from copy import deepcopy

import pytest
from evograph.application.plan_harness import (
    _semantic_result,
    replay_harness,
    run_harness,
    seal_snapshot,
)
from evograph.application.plan_repair_context import build_repair_agenda
from evograph.application.plan_review import (
    BATCH_VERSION,
    CHECKER_VERSION,
    SCOPED_BATCH_VERSION,
    BatchSemanticReview,
    ScopedBatchSemanticReview,
    batch_review_certificate,
    batch_review_packet,
    normalize_batch_review,
    validate_batch_certificate,
)
from evograph.application.plan_validation import build_validation_receipt
from evograph.domain.models import Project
from evograph.domain.plan_harness import CURRENT_POLICY, HarnessRun, content_hash, policy_decision


@pytest.fixture
def context():
    text = "Display eligible available times and atomically book an eligible technician."
    project = Project.model_validate({
        "id": "materiality-fixture", "name": "Booking", "targets": [
            {"number": 1, "statement": text, "required_behavior_ids": ["B1", "B2"]}],
        "milestones": [
            {"id": "M1", "title": "Booking", "intent": text,
             "scope": ["Availability rendering and atomic booking"],
             "architecture_components": ["core"], "architecture_revision": 1,
             "behavior_revision_ids": ["B1"]},
            {"id": "M2", "title": "Staff timeline", "intent": "Inspect technician day",
             "scope": ["Staff timeline"], "dependencies": ["M1"],
             "dependency_reasons": {"M1": "TrueTrue Reuse M1 availability predicate"},
             "architecture_components": ["core"], "architecture_revision": 1,
             "behavior_revision_ids": ["B2"]}],
        "behaviors": [
            {"id": "B1", "version": 1, "behavior_key": "booking", "owner": "M1", "statement": text},
            {"id": "B2", "version": 1, "behavior_key": "timeline", "owner": "M2",
             "statement": "Staff inspect available technician times"}],
        "plan_contract": {"sources": [{"id": "source", "text": text}],
            "requirements": [{"id": "eligibility", "source_id": "source", "quote": text}],
            "bindings": [
                {"behavior_key": "booking", "behavior_revision_id": "B1",
                 "requirement_ids": ["eligibility"], "component_ids": ["core"],
                 "mechanism": "Within one transaction pick first interval-free technician; eligibility rule undecided"},
                {"behavior_key": "timeline", "behavior_revision_id": "B2",
                 "requirement_ids": ["eligibility"], "component_ids": ["core"],
                 "mechanism": "Read eligible technicians using the shared availability predicate"}]},
        "architectures": [{"number": 1, "summary": "Local booking and rendering",
            "diagram": {"id": "design", "title": "Booking", "milestone_ids": ["M1", "M2"],
                        "nodes": [{"id": "core", "label": "Core", "description": "Booking data and predicate"}]},
            "technologies": [{"area": "storage", "choice": "SQLite", "rationale": "Local atomic commits"}]}],
    })
    record = {"id": "candidate", "base_revision": 0, "input": text,
              "checker_version": CHECKER_VERSION}
    snapshot = seal_snapshot(project, project, record)
    packet = batch_review_packet(project, project, record)
    return project, record, snapshot, packet


def material_issue(subjects=("slice_activation:M1",), verdict="unknown"):
    return {"id": "eligibility-gap", "subjects": list(subjects),
            "materiality": {"obligation_ref": "/candidate/behaviors/0/statement",
                "obligation_excerpt": "book an eligible technician", "affected_owner_ids": ["M1"],
                "gap_kind": "explicit_conflict" if verdict == "contradicted" else "unresolved_semantics",
                "boundary_refs": ["/candidate/plan_contract/bindings/0/mechanism"],
                "necessary_plan_change": "Choose one eligibility policy used by both display and allocation"},
            "verdict": verdict, "reason": "Choosing eligibility changes observable allocation and needs a plan decision",
            "counterexample": "One free technician is ineligible; reader and allocator disagree",
            "evidence_refs": ["/candidate/behaviors/0", "/candidate/plan_contract/bindings/0"]}


def observation(kind="editorial"):
    return {"id": "dependency-text", "subjects": ["slice_activation:M2"], "kind": kind,
            "reason": "Remove stray words; M1 dependency and the shared predicate stay unchanged",
            "basis": "source_statement", "evidence_refs": ["/candidate/milestones/1/dependency_reasons/M1"]}


def batch_for(packet, *, issues=(), observations=()):
    unsupported = {s: issue["verdict"] for issue in issues for s in issue["subjects"]}
    value = {
        "candidate_hash": packet["candidate_hash"], "review_scope_hash": packet["review_scope_hash"],
        "summary": "Offline materiality fixture; no implementation executed",
        "statuses": {status: [s for s in packet["required_subjects"]
                              if unsupported.get(s, "supported") == status]
                     for status in ("supported", "contradicted", "unknown")},
        "issues": list(issues), "observations": list(observations)}
    if packet.get("review_protocol") == SCOPED_BATCH_VERSION:
        value["coverage"] = {s: verdict for verdict, subjects in value.pop("statuses").items()
                             for s in subjects}
        return ScopedBatchSemanticReview.model_validate(value)
    return BatchSemanticReview.model_validate(value)


def review_and_certificate(context, batch):
    project, _, _, packet = context
    review = normalize_batch_review(project, packet, batch)
    # Preserve actual transport bytes rather than reconstructing a canonical raw string.
    raw = json.dumps(batch.model_dump(), ensure_ascii=False, indent=2)
    return review, batch_review_certificate(raw, batch)


def harness(context, batch):
    _, _, snapshot, _ = context
    async def evaluate(_):
        return review_and_certificate(context, batch)
    return asyncio.run(run_harness(snapshot, evaluate))


@pytest.mark.parametrize("verdict,plugin_verdict", [("unknown", "unknown"), ("contradicted", "block")])
def test_real_material_gap_stays_held(context, verdict, plugin_verdict):
    run = harness(context, batch_for(context[3], issues=[material_issue(verdict=verdict)]))
    row = run.executions[-1]
    assert row.status == "completed", run.model_dump()
    assert row.result.verdict == plugin_verdict
    assert run.decision == "hold"
    assert all(f.severity == "error" for f in row.result.findings)


@pytest.mark.parametrize("kind", ["editorial", "implementation_latitude"])
def test_supported_advisory_passes_without_claiming_implementation_run(context, kind):
    batch = batch_for(context[3], observations=[observation(kind)])
    run = harness(context, batch)
    assert run.decision == "apply", run.model_dump()
    assert run.executions[-1].result.verdict == "pass"
    assert {f.severity for f in run.executions[-1].result.findings} == {"review"}
    assert build_repair_agenda(run, context[2])["plugins"] == []
    receipt = build_validation_receipt(context[0], harness_run=run)
    assert receipt["implementation"]["status"] == "not_run"
    assert receipt["model"]["status"] == "no_issue_found"
    assert json.loads(run.model_certificate_json)["batch"]["observations"] == batch.model_dump()["observations"]


def test_advisory_cannot_cover_unknown_and_malformed_review_is_not_a_repair(context):
    batch = batch_for(context[3], observations=[observation()])
    subject = batch.statuses.supported.pop()
    batch.statuses.unknown.append(subject)
    with pytest.raises(ValueError, match="精确覆盖每个未通过项"):
        normalize_batch_review(context[0], context[3], batch)
    # Evaluator protocol failure becomes invalid_output, never semantic_unknown.
    async def malformed(_):
        return review_and_certificate(context, batch)
    run = asyncio.run(run_harness(context[2], malformed))
    assert run.decision == "hold" and run.executions[-1].status == "invalid_output"
    assert run.executions[-1].result is None and run.semantic_review_json is None
    with pytest.raises(ValueError, match="controlled retry"):
        build_repair_agenda(run, context[2])


@pytest.mark.parametrize("consumer,provider,valid", [
    ("M1", "/candidate/behaviors/0", False),  # own work is available
    ("M2", "/candidate/plan_contract/bindings/0", False),  # declared ancestor
    ("M1", "/candidate/milestones/1", True),  # future stage
])
def test_unavailable_provider_must_be_outside_owner_ancestor_closure(context, consumer, provider, valid):
    issue = material_issue(subjects=("slice_activation:" + consumer,))
    issue["materiality"].update({"obligation_ref": "/current_input",
        "affected_owner_ids": [consumer], "gap_kind": "unavailable_prerequisite",
        "consumer_owner_id": consumer, "provider_ref": provider, "boundary_refs": [provider]})
    batch = batch_for(context[3], issues=[issue])
    if valid:
        assert normalize_batch_review(context[0], context[3], batch)
    else:
        with pytest.raises(ValueError, match="自身或声明祖先"):
            normalize_batch_review(context[0], context[3], batch)


def test_missing_provider_can_remain_unresolved_without_inventing_identity(context):
    issue = material_issue()
    issue["reason"] = "No provider is named; alternatives change eligibility semantics"
    assert normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))
    issue["materiality"]["gap_kind"] = "unavailable_prerequisite"
    with pytest.raises(ValueError, match="消费者"):
        normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))


@pytest.mark.parametrize("change", [
    {"obligation_excerpt": "Invented obligation"},
    {"obligation_ref": "/candidate/behaviors/100/statement"},
    {"obligation_ref": "/candidate/behaviors/0/id", "obligation_excerpt": "B1"},
    {"affected_owner_ids": ["invented"]},
    {"affected_owner_ids": []},
    {"affected_owner_ids": ["M1", "M1"]},
    {"boundary_refs": ["/candidate/plan_contract/bindings/0/missing"]},
])
def test_materiality_provenance_is_mechanically_checked(context, change):
    issue = material_issue()
    issue["materiality"].update(change)
    with pytest.raises(ValueError):
        normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))


def test_mixed_review_projects_only_material_issues_to_repair(context):
    batch = batch_for(context[3], issues=[material_issue()], observations=[observation()])
    run = harness(context, batch)
    assert {f.severity for f in run.executions[-1].result.findings} == {"error", "review"}
    agenda = build_repair_agenda(run, context[2])
    plugin, = agenda["plugins"]
    assert plugin["issues"] == batch.model_dump()["issues"]
    assert "dependency-text" not in json.dumps(agenda)
    assert "dependency-text" in run.model_certificate_json


def test_versions_hashes_and_raw_replay_are_current_and_exact(context):
    project, record, snapshot, packet = context
    batch = batch_for(packet, observations=[observation()])
    review, certificate = review_and_certificate(context, batch)
    stored = {**record, "review_inputs": [packet], "batch_reviews": [certificate],
              "report": {"semantic_batch": batch.model_dump(), "semantic": review.model_dump()},
              "reviews": [review.model_dump()]}
    assert BATCH_VERSION == "semantic-batch/v2"
    assert packet["checker_version"] == CHECKER_VERSION and CHECKER_VERSION.endswith("v13")
    assert validate_batch_certificate(project, project, stored) == review
    run = harness(context, batch)
    replayed = replay_harness(snapshot, run, lambda *_: validate_batch_certificate(project, project, stored))
    assert replayed.decision == "apply"
    assert replayed.executions[-1].plugin_version == "3"
    for change in ({"checker_version": "unified-contract-challenge-v11"},
                   {"batch_reviews": [{**certificate, "schema_version": "semantic-batch/v1"}]},
                   {"input": "Changed scope"}):
        with pytest.raises(ValueError):
            validate_batch_certificate(project, project, {**stored, **change})
    changed = deepcopy(stored)
    changed["batch_reviews"][-1]["raw_arguments"] = certificate["raw_arguments"].replace(
        "Remove stray words", "Changed raw observation")
    with pytest.raises(ValueError, match="原始结果"):
        validate_batch_certificate(project, project, changed)
    stale = batch.model_copy(deep=True)
    stale.candidate_hash = "old"
    with pytest.raises(ValueError, match="过期"):
        normalize_batch_review(project, packet, stale)
    stale.candidate_hash, stale.review_scope_hash = packet["candidate_hash"], "old"
    with pytest.raises(ValueError, match="过期"):
        normalize_batch_review(project, packet, stale)
    # Raw-byte changes which parse identically still invalidate the harness certificate ID.
    altered_certificate = {**certificate, "raw_arguments": certificate["raw_arguments"] + " "}
    altered_run = run.model_copy(update={"model_certificate_json": json.dumps(altered_certificate)})
    replayed = replay_harness(snapshot, altered_run, lambda *_: review)
    assert replayed.decision == "hold" and replayed.executions[-1].status == "invalid_output"
    # Old schemas remain data-readable; they cannot authorize current publication.
    legacy = json.loads(run.model_dump_json())
    legacy["policy_version"] = "plan-harness-policy/v5"
    assert HarnessRun.model_validate_json(json.dumps(legacy)).policy_version.endswith("v5")
    with pytest.raises(ValueError, match="policy changed"):
        replay_harness(snapshot, legacy, lambda *_: review)


def test_optional_model_failure_is_not_promoted_into_required_repair(context):
    async def invalid(_):
        raise ValueError("Offline invalid protocol fixture")
    run = asyncio.run(run_harness(context[2], invalid))
    optional = CURRENT_POLICY.model_copy(update={"version": "fixture-optional-model",
                                                 "require_model_opinion": False})
    optional_run = run.model_copy(update={"policy_version": optional.version,
        "policy_hash": content_hash(optional.model_dump(mode="json"))})
    assert policy_decision(run.executions, policy=optional) == "apply"
    assert build_repair_agenda(optional_run, context[2], policy=optional)["plugins"] == []
    with pytest.raises(ValueError, match="controlled retry"):
        build_repair_agenda(run, context[2])


@pytest.mark.parametrize("declared", [True, False])
def test_source_provider_closure_and_source_owner_references(context, declared):
    from evograph.domain.models import Milestone
    project = context[0].model_copy(deep=True)
    project.source_milestones = [Milestone(id="SRC_STORE", title="Source store", intent="Existing store",
        origin="source", source_baseline_id="baseline-fixture",
        source_behaviors=[{"key": "store", "statement": "Read technician eligibility",
                           "source_refs": ["store.py"]}])]
    if declared:
        project.milestones[0].dependencies = ["SRC_STORE"]
    packet = batch_review_packet(project, project, context[1])
    provider_ref = "/candidate/source_milestones/0/source_behaviors/0"
    issue = material_issue()
    issue["materiality"].update({"gap_kind": "unavailable_prerequisite",
        "consumer_owner_id": "M1", "provider_ref": provider_ref,
        "affected_owner_ids": ["M1", "SRC_STORE"], "boundary_refs": [provider_ref]})
    if declared:
        with pytest.raises(ValueError, match="自身或声明祖先"):
            normalize_batch_review(project, packet, batch_for(packet, issues=[issue]))
    else:
        assert normalize_batch_review(project, packet, batch_for(packet, issues=[issue]))
    # Source ownership is a valid explicit identity even when the issue is a
    # semantic boundary, not a claim of an unavailable prerequisite.
    issue["materiality"].update({"gap_kind": "unresolved_semantics",
        "consumer_owner_id": None, "provider_ref": None,
        "obligation_ref": provider_ref + "/statement", "obligation_excerpt": "technician eligibility"})
    assert normalize_batch_review(project, packet, batch_for(packet, issues=[issue]))


@pytest.mark.parametrize("change", [
    {"subjects": ["invented_subject"]},
    {"subjects": ["slice_activation:M2", "slice_activation:M2"]},
    {"basis": "existing_execution_record", "execution_evidence_ids": []},
    {"basis": "existing_execution_record", "execution_evidence_ids": ["invented"]},
    {"basis": "source_statement", "execution_evidence_ids": ["invented"]},
    {"evidence_refs": ["/candidate/milestones/90"]},
])
def test_observation_basis_refs_and_subjects_are_validated(context, change):
    row = observation()
    row.update(change)
    with pytest.raises(ValueError):
        normalize_batch_review(context[0], context[3], batch_for(context[3], observations=[row]))


def test_no_saved_unknown_rewrite_or_keyword_downgrade(context):
    issue = material_issue()
    issue["reason"] = "TrueTrue is part of the reported issue; semantic impact is still the reviewer's judgment"
    run = harness(context, batch_for(context[3], issues=[issue]))
    assert run.decision == "hold" and run.executions[-1].result.verdict == "unknown"
    assert "TrueTrue" in next(c for c in json.loads(run.semantic_review_json)["checks"]
                              if c["subject"] == "slice_activation:M1")["reason"]


def test_v1_shapes_and_normalized_tamper_cannot_authorize(context):
    batch = batch_for(context[3], issues=[material_issue()])
    raw = batch.model_dump()
    del raw["observations"]
    del raw["issues"][0]["materiality"]
    with pytest.raises(ValueError):
        BatchSemanticReview.model_validate(raw)
    review, certificate = review_and_certificate(context, batch)
    review.checks[-2].reason = "Changed normalized opinion"
    with pytest.raises(ValueError, match="normalized semantic review differ"):
        _semantic_result(context[2], review, certificate)


def test_historical_attachment_literal_obligation_is_not_forced_into_a_new_requirement(context):
    project = context[0].model_copy(deep=True)
    project.plan_contract.sources[0].reference_context = {
        "attachments": [{"id": "saved-attachment", "text": "Reject ineligible bookings"}]}
    packet = batch_review_packet(project, project, context[1])
    issue = material_issue()
    issue["materiality"].update({
        "obligation_ref": "/candidate/plan_contract/sources/0/reference_context/attachments/0/text",
        "obligation_excerpt": "Reject ineligible bookings"})
    assert normalize_batch_review(project, packet, batch_for(packet, issues=[issue]))
