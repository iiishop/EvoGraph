"""Pure synthetic projects: reference existing acceptance, never run validation code."""
from copy import deepcopy

import pytest
from evograph.application.plan_validation import build_validation_receipt
from evograph.domain.models import Baseline, BehaviorRevision, Evidence, Milestone, Project
from evograph.domain.plan_contracts import (
    SemanticCheck,
    SemanticReview,
    candidate_hash,
    eligible_execution_evidence,
    review_subjects,
    validate_semantic_review,
)
from pydantic import ValidationError


def project_with_records(*results, project_id="project-one"):
    behavior = BehaviorRevision(id="behavior-one", behavior_key="temperature", version=1,
                                statement="0 Celsius converts to 32 Fahrenheit", owner="CONVERT")
    milestone = Milestone(id="CONVERT", title="Pure converter", intent="Convert temperature",
                          scope=["temperature.py"], behavior_revision_ids=[behavior.id])
    baseline = Baseline(id="baseline-one", number=1, commit="synthetic-commit", fingerprint="synthetic-fingerprint",
                        file_count=1, complete=True)
    records = [Evidence(id=f"evidence-{number}", milestone_id=milestone.id,
                        behavior_revision_ids=[behavior.id], baseline_id=baseline.id,
                        fingerprint=baseline.fingerprint, architecture_revision=0,
                        command=["synthetic-fixture"], result=result, output=f"Synthetic {result} record",
                        duration=0.25, provider="synthetic", request_id=f"request-{number}")
               for number, result in enumerate(results or ["PASS"], 1)]
    return Project(id=project_id, name="Synthetic converter", milestones=[milestone], behaviors=[behavior],
                   baselines=[baseline], evidence=records, plan_contract={
                       "sources": [{"id": "source-one", "text": "Convert Celsius to Fahrenheit"}],
                       "requirements": [{"id": "requirement-one", "source_id": "source-one",
                                         "quote": "Convert Celsius to Fahrenheit"}],
                       "bindings": [{"behavior_key": behavior.behavior_key, "behavior_revision_id": behavior.id,
                                     "requirement_ids": ["requirement-one"], "mechanism": "Pure arithmetic"}],
                   })


def review_for(project, *, basis=None, evidence_ids=None, verdict="supported"):
    raw = {"candidate_hash": candidate_hash(project), "summary": "Synthetic model opinion",
           "checks": [{"subject": subject, "verdict": verdict,
                       "reason": "Model comparison of the saved contract", "counterexample": "An invalid input may violate this boundary"}
                      for subject in review_subjects(project)]}
    if basis is not None:
        raw["checks"][0]["basis"] = basis
    if evidence_ids is not None:
        raw["checks"][0]["execution_evidence_ids"] = evidence_ids
    return SemanticReview.model_validate(raw)


def test_legacy_review_and_candidate_documents_remain_compatible():
    project = project_with_records()
    before = project.model_dump()
    review = review_for(project)
    assert all(check.basis == "model_inference" and check.execution_evidence_ids == [] for check in review.checks)
    assert validate_semantic_review(project, review)
    assert Project.model_validate(before).model_dump() == before
    assert project.model_dump() == before
    assert "validation_receipt" not in project.model_dump()


def test_all_current_pass_fail_error_records_are_eligible_without_preference_or_mutation():
    project = project_with_records("PASS", "FAIL", "ERROR")
    before = project.model_dump()
    records = eligible_execution_evidence(project)
    assert records == [{"project_id": project.id, **e.model_dump()} for e in project.evidence]
    assert [e["result"] for e in records] == ["PASS", "FAIL", "ERROR"]
    records[0]["command"].append("not executed")
    records[0]["behavior_revision_ids"].clear()
    records[0]["output"] = "not saved"
    assert project.model_dump() == before


@pytest.mark.parametrize("result", ["PASS", "FAIL", "ERROR"])
@pytest.mark.parametrize("verdict", ["supported", "contradicted", "unknown"])
def test_existing_record_reference_does_not_rewrite_its_result_or_determine_model_verdict(result, verdict):
    project = project_with_records(result)
    before = project.model_dump()
    review = review_for(project, basis="existing_execution_record", evidence_ids=["evidence-1"], verdict=verdict)
    assert validate_semantic_review(project, review) is (verdict == "supported")
    assert project.model_dump() == before
    assert project.evidence[0].result == result


@pytest.mark.parametrize("basis", ["model_inference", "source_statement"])
def test_non_execution_basis_cannot_attach_execution_record_ids(basis):
    project = project_with_records()
    with pytest.raises(ValidationError, match="仅 existing_execution_record"):
        review_for(project, basis=basis, evidence_ids=["evidence-1"])
    assert validate_semantic_review(project, review_for(project, basis=basis))


@pytest.mark.parametrize("ids", [[], ["evidence-1", "evidence-1"]])
def test_execution_basis_requires_nonempty_unique_ids(ids):
    with pytest.raises(ValidationError):
        review_for(project_with_records(), basis="existing_execution_record", evidence_ids=ids)


@pytest.mark.parametrize("basis", ["executed_this_turn", "implementation_verified", "test_run", "PASS"])
def test_schema_has_no_basis_for_model_to_claim_current_turn_execution(basis):
    with pytest.raises(ValidationError):
        review_for(project_with_records(), basis=basis)


def test_model_cannot_supply_authoritative_server_receipt_fields():
    raw = review_for(project_with_records()).model_dump()
    for key, value in [("implementation", {"status": "passed"}), ("validation_receipt", {}), ("executed", True)]:
        with pytest.raises(ValidationError, match="Extra inputs"):
            SemanticReview.model_validate({**raw, key: value})
    check = raw["checks"][0]
    with pytest.raises(ValidationError, match="Extra inputs"):
        SemanticCheck.model_validate({**check, "executed_this_turn": True})


def test_unknown_and_cross_project_execution_references_are_rejected():
    local, other = project_with_records(), project_with_records(project_id="other-project")
    other.evidence[0].id = "other-project-record"
    other_before = other.model_dump()
    for evidence_id in ["missing", other.evidence[0].id, ""]:
        review = review_for(local, basis="existing_execution_record", evidence_ids=[evidence_id])
        with pytest.raises(ValueError, match="不存在于本项目"):
            validate_semantic_review(local, review)
    assert other.model_dump() == other_before


def invalidate(project, case):
    record = project.evidence[0]
    if case == "no_baseline":
        project.baselines = []
    elif case == "incomplete_baseline":
        project.baseline.complete = False
    elif case == "blank_baseline_fingerprint":
        project.baseline.fingerprint = record.fingerprint = ""
    elif case == "duplicate_baseline_id":
        project.baselines.append(project.baseline.model_copy(deep=True))
    elif case == "changed_current_baseline":
        project.baselines.append(project.baseline.model_copy(update={"id": "baseline-two", "number": 2}))
    elif case == "fingerprint_mismatch":
        record.fingerprint = "stale-fingerprint"
    elif case == "owner_removed":
        project.milestones = []
    elif case == "owner_is_source":
        project.milestones[0].origin = "source"
    elif case == "wrong_owner":
        record.milestone_id = "missing-owner"
    elif case == "wrong_behavior_owner":
        project.behaviors[0].owner = "other-owner"
    elif case == "stale_architecture":
        project.milestones[0].architecture_revision = 2
    elif case == "empty_behavior_set":
        record.behavior_revision_ids = []
    elif case == "partially_stale_behavior_set":
        record.behavior_revision_ids.append("retired-behavior")
    elif case == "inactive_behavior":
        project.milestones[0].behavior_revision_ids = []
    elif case == "missing_behavior":
        project.behaviors = []
    elif case == "duplicate_behavior_reference":
        record.behavior_revision_ids *= 2
    elif case == "duplicate_behavior_identity":
        project.behaviors.append(project.behaviors[0].model_copy(deep=True))
    elif case == "duplicate_active_reference":
        project.milestones.append(project.milestones[0].model_copy(update={"id": "OTHER"}, deep=True))
    elif case == "duplicate_owner_identity":
        project.milestones.append(project.milestones[0].model_copy(deep=True))
    elif case == "duplicate_evidence_identity":
        project.evidence.append(record.model_copy(deep=True))
    elif case == "blank_evidence_identity":
        record.id = ""
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "no_baseline", "incomplete_baseline", "blank_baseline_fingerprint", "duplicate_baseline_id",
    "changed_current_baseline", "fingerprint_mismatch", "owner_removed", "owner_is_source",
    "wrong_owner", "wrong_behavior_owner", "stale_architecture", "empty_behavior_set",
    "partially_stale_behavior_set", "inactive_behavior", "missing_behavior", "duplicate_behavior_reference",
    "duplicate_behavior_identity", "duplicate_active_reference", "duplicate_owner_identity",
    "duplicate_evidence_identity", "blank_evidence_identity",
])
def test_invalid_or_stale_records_never_enter_allowlist_or_review(case):
    project = project_with_records()
    invalidate(project, case)
    before = project.model_dump()
    assert eligible_execution_evidence(project) == []
    review = review_for(project, basis="existing_execution_record", evidence_ids=["evidence-1"])
    with pytest.raises(ValueError, match="不存在于本项目"):
        validate_semantic_review(project, review)
    assert project.model_dump() == before


def test_revised_behavior_stales_evidence_but_unused_historical_revision_does_not():
    project = project_with_records()
    historical = project.behaviors[0].model_copy(update={"id": "behavior-two", "version": 2,
                                "supersedes": "behavior-one", "statement": "Alternate unused version"})
    project.behaviors.append(historical)
    assert [e["id"] for e in eligible_execution_evidence(project)] == ["evidence-1"]
    project.milestones[0].behavior_revision_ids = [historical.id]
    assert eligible_execution_evidence(project) == []


def test_subset_record_pins_only_existing_active_behaviors_of_its_owner():
    project = project_with_records()
    additional = project.behaviors[0].model_copy(update={"id": "another-active", "behavior_key": "format"})
    project.behaviors.append(additional)
    project.milestones[0].behavior_revision_ids.append(additional.id)
    assert [e["id"] for e in eligible_execution_evidence(project)] == ["evidence-1"]


def test_revalidation_rejects_mutated_basis_or_removed_execution_evidence():
    project = project_with_records()
    review = review_for(project)
    review.checks[0].execution_evidence_ids = ["evidence-1"]
    with pytest.raises(ValueError, match="仅 existing_execution_record"):
        validate_semantic_review(project, review)
    review.checks[0].basis = "existing_execution_record"
    assert validate_semantic_review(project, review)
    project.evidence.clear()  # Not included in planning hash; applicability must still be rechecked.
    with pytest.raises(ValueError, match="不存在于本项目"):
        validate_semantic_review(project, review)


@pytest.mark.parametrize("findings,status,count", [(None, "not_run", None), ([], "clear", 0),
    ([{"code": "missing_link"}], "issues", 1)])
def test_server_receipt_distinguishes_structural_check_states(findings, status, count):
    project = project_with_records("PASS", "FAIL", "ERROR")
    before = project.model_dump()
    receipt = build_validation_receipt(project, structural_findings=findings)
    assert receipt == {
        "schema_version": "planning-validation/v1", "candidate_hash": candidate_hash(project),
        "structural": {"status": status, "finding_count": count},
        "model": {"kind": "model_opinion", "status": "not_run", "subject_count": 0},
        "implementation": {"scope": "current_planning_turn", "status": "not_run", "existing_record_count": 3},
    }
    assert project.model_dump() == before


@pytest.mark.parametrize("verdict,status", [("supported", "no_issue_found"), ("contradicted", "issues"), ("unknown", "issues")])
def test_receipt_never_converts_model_opinion_or_existing_records_into_new_execution(verdict, status):
    project = project_with_records("PASS")
    review = review_for(project, basis="existing_execution_record", evidence_ids=["evidence-1"], verdict=verdict)
    before = project.model_dump()
    receipt = build_validation_receipt(project, structural_findings=[], semantic_review=review)
    assert receipt["model"] == {"kind": "model_opinion", "status": status, "subject_count": len(review_subjects(project))}
    assert receipt["implementation"] == {"scope": "current_planning_turn", "status": "not_run", "existing_record_count": 1}
    assert project.model_dump() == before


@pytest.mark.parametrize("invalid", ["malformed", "wrong_hash", "wrong_subjects", "missing_evidence", "mutated_basis", "explicit_unavailable"])
def test_bad_semantics_receipt_is_unavailable_not_no_issue_found(invalid):
    project = project_with_records()
    review = review_for(project)
    if invalid == "malformed":
        review = {"summary": "No usable review"}
    elif invalid == "wrong_hash":
        review.candidate_hash = "old-candidate"
    elif invalid == "wrong_subjects":
        review.checks = review.checks[:-1]
    elif invalid == "missing_evidence":
        review.checks[0].basis = "existing_execution_record"
        review.checks[0].execution_evidence_ids = ["invented"]
    elif invalid == "mutated_basis":
        review.checks[0].basis = "executed_this_turn"
    receipt = build_validation_receipt(project, semantic_review=review, semantic_unavailable=invalid == "explicit_unavailable")
    assert receipt["model"] == {"kind": "model_opinion", "status": "unavailable", "subject_count": 0}
    assert receipt["implementation"]["status"] == "not_run"


def test_receipt_binding_changes_with_candidate_and_old_records_are_not_backfilled():
    project = project_with_records()
    old_review = review_for(project)
    old_record = {"project": project.model_dump(), "report": {"semantic": old_review.model_dump()}}
    unchanged = deepcopy(old_record)
    receipt = build_validation_receipt(project, semantic_review=old_review)
    project.milestones[0].intent = "Changed contract context"
    current = build_validation_receipt(project, semantic_review=old_review)
    assert current["candidate_hash"] != receipt["candidate_hash"]
    assert current["model"]["status"] == "unavailable"
    assert old_record == unchanged and "validation_receipt" not in old_record


def test_existing_record_count_excludes_stale_history_without_claiming_never_implemented():
    project = project_with_records("PASS")
    project.milestones[0].architecture_revision += 1
    receipt = build_validation_receipt(project)
    assert len(project.evidence) == 1 and receipt["implementation"]["existing_record_count"] == 0
    assert receipt["implementation"]["scope"] == "current_planning_turn"
    assert receipt["implementation"]["status"] == "not_run"
