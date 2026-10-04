"""Server-authored planning receipts, deliberately separate from model opinions."""

from ..domain.plan_contracts import (
    SemanticReview,
    candidate_hash,
    eligible_execution_evidence,
    validate_semantic_review,
)


def build_validation_receipt(project, *, structural_findings=None, semantic_review=None,
                             semantic_unavailable=False):
    """Describe checks of this candidate without claiming this turn ran code.

    The caller supplies the deterministic check's findings: None means not run,
    whereas [] means clear. A malformed/stale semantic result is unavailable, not
    a favorable review. The record count covers current eligible stored records,
    regardless of PASS/FAIL/ERROR; it says nothing about historical implementation
    absence, record truth, test success, or work executed in this planning turn.
    This pure helper neither persists a receipt nor retrofits historical records.
    """
    model_status = "unavailable" if semantic_unavailable else "not_run"
    subject_count = 0
    if semantic_review is not None and not semantic_unavailable:
        try:
            # Validate a copy even for existing model instances: Pydantic models
            # can be edited after initial parsing without assignment validation.
            raw = semantic_review.model_dump() if isinstance(semantic_review, SemanticReview) else semantic_review
            review = SemanticReview.model_validate(raw)
            supported = validate_semantic_review(project, review)
        except (ValueError, TypeError):
            model_status = "unavailable"
        else:
            model_status = "no_issue_found" if supported else "issues"
            subject_count = len(review.checks)
    return {
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
