"""Server-authored planning receipts, deliberately separate from model opinions."""

import json

from ..domain.plan_contracts import (
    SemanticReview,
    candidate_hash,
    eligible_execution_evidence,
    validate_semantic_review,
)


def build_validation_receipt(project, *, structural_findings=None, semantic_review=None,
                             semantic_unavailable=False, harness_run=None):
    """Describe checks of this candidate without claiming this turn ran code.

    The caller supplies the deterministic check's findings: None means not run,
    whereas [] means clear. A malformed/stale semantic result is unavailable, not
    a favorable review. The record count covers current eligible stored records,
    regardless of PASS/FAIL/ERROR; it says nothing about historical implementation
    absence, record truth, test success, or work executed in this planning turn.
    This pure helper neither persists a receipt nor retrofits historical records.
    """
    harness = None
    required_subjects = None
    if harness_run is not None:
        from ..domain.plan_harness import DETERMINISTIC_REQUIRED, HarnessRun, execution_satisfied
        raw_run = harness_run.model_dump(mode="json") if isinstance(harness_run, HarnessRun) else harness_run
        run = HarnessRun.model_validate_json(json.dumps(raw_run, ensure_ascii=False))
        if run.candidate_hash != candidate_hash(project):
            raise ValueError("检查回执与候选版本不一致")
        programs = [row for row in run.executions if row.kind == "deterministic"]
        program_findings = [f for row in programs if row.result
                            for f in row.result.findings if f.severity == "error"]
        structural_findings = program_findings if program_findings else (
            [] if {row.plugin_id for row in programs} == set(DETERMINISTIC_REQUIRED) and all(execution_satisfied(row) for row in programs) else None)
        model = next((row for row in run.executions if row.kind == "model_opinion"), None)
        required_subjects = model.result.covered_subjects if model and model.result else None
        semantic_review = json.loads(run.semantic_review_json) if run.semantic_review_json else None
        semantic_unavailable = bool(model and model.status not in {
            "completed", "prerequisite_skipped", "disabled_by_policy"})
        harness = {
            "schema_version": "planning-harness-disclosure/v1",
            "snapshot_id": run.snapshot_id, "policy_version": run.policy_version,
            "decision": run.decision,
            "checks": [{
                "id": row.plugin_id, "kind": row.kind, "version": row.plugin_version,
                "execution": row.status, "verdict": row.result.verdict if row.result else None,
                "findings": len(row.result.findings) if row.result else 0,
                "message": row.detail or (row.result.applicability_reason if row.result else ""),
            } for row in run.executions],
        }
    model_status = "unavailable" if semantic_unavailable else "not_run"
    subject_count = 0
    if semantic_review is not None and not semantic_unavailable:
        try:
            # Validate a copy even for existing model instances: Pydantic models
            # can be edited after initial parsing without assignment validation.
            raw = semantic_review.model_dump() if isinstance(semantic_review, SemanticReview) else semantic_review
            review = SemanticReview.model_validate(raw)
            supported = validate_semantic_review(project, review, required_subjects=required_subjects)
        except (ValueError, TypeError):
            model_status = "unavailable"
        else:
            model_status = "no_issue_found" if supported else "issues"
            subject_count = len(review.checks)
    receipt = {
        "schema_version": "planning-validation/v1",
        "candidate_hash": candidate_hash(project),
        "structural": {
            "status": "not_run" if structural_findings is None else "issues" if structural_findings else "clear",
            "finding_count": None if structural_findings is None else len(structural_findings),
        },
        "model": {"kind": "model_opinion", "status": model_status, "subject_count": subject_count},
        "implementation": {
            "scope": "current_planning_turn", "status": "not_run",
            "existing_record_count": len(eligible_execution_evidence(project)),
        },
    }

    if harness is not None:
        receipt["harness"] = harness
    return receipt
