import pytest
from conftest import accept_external, apply_proposal, proposal
from evograph.domain.models import Evidence, Milestone
from evograph.domain.policies import (
    acceptance,
    impact_closure,
    readiness,
    retirement_disposition,
    validate_plan,
)
from evograph.infrastructure.database import ConflictError, Database


def test_v1_v2_keeps_behavior_identity_and_invalidates_old_proof(app, planned, repository):
    app.execution.start(planned.id, "M01")
    result = accept_external(app, planned.id, "M01")
    assert result["result"] == "PASS"
    p = app.db.get(planned.id)
    old_behavior = p.behaviors[-1]
    old_evidence = p.evidence[-1]
    assert acceptance(p)["achieved"]
    (repository / "auth.py").write_text("def login(email=None): return bool(email)\n")
    p = app.execution.refresh(p.id)
    assert not acceptance(p)["achieved"]
    assert p.evidence[-1] == old_evidence  # Historical run is immutable.
    assert p.milestone("M01").status == "REVALIDATION_REQUIRED"
    app.execution.release(p.id, "M01")
    p = apply_proposal(app, p, proposal("Login V2", "Email login succeeds", "M02"))
    current = p.behaviors[-1]
    assert current.behavior_key == old_behavior.behavior_key
    assert current.version == 2 and current.supersedes == old_behavior.id
    assert current.owner == "M02"
    assert len(p.targets) == 2 and len(p.plans) == 2
    app.execution.resolve_obligation(p.id, "M02", "scope", True, "Reviewed email login change")
    app.execution.start(p.id, "M02")
    accept_external(app, p.id, "M02")
    p = app.db.get(p.id)
    assert acceptance(p)["achieved"]
    assert len(p.evidence) == 2
    assert app.projects.get(p.id)["evidence_validity"][old_evidence.id] == "STALE"


def test_logical_ready_does_not_imply_safe_parallel_execution(app, planned):
    p = app.db.get(planned.id)
    p.milestones.append(
        Milestone(id="M02", title="Parallel", intent="Another change", resources=["api:auth"])
    )
    app.db.save(p, "test")
    app.execution.start(p.id, "M01")
    p = app.db.get(p.id)
    state = readiness(p, "M02")
    assert state["logical_ready"]
    assert not state["safe_to_execute"]
    assert state["conflicts"] == ["M01"]
    assert not p.milestone("M02").dependencies  # No synthetic prerequisite edge.


def test_stale_completed_work_does_not_hold_a_resource_lease(app, planned, repository):
    app.execution.start(planned.id, "M01")
    accept_external(app, planned.id, "M01")
    (repository / "new.py").write_text("value = 1")
    p = app.execution.refresh(planned.id)
    assert p.milestone("M01").status == "REVALIDATION_REQUIRED"
    assert not p.milestone("M01").lease_active


def test_fail_after_pass_is_not_valid_evidence(app, planned):
    p = app.db.get(planned.id)
    m = p.milestone("M01")
    for result in ["PASS", "FAIL"]:
        p.evidence.append(
            Evidence(
                milestone_id=m.id,
                behavior_revision_ids=m.behavior_revision_ids,
                baseline_id=p.baseline.id,
                fingerprint=p.baseline.fingerprint,
                command=["test"],
                result=result,
                output="",
                duration=0,
            )
        )
    assert not acceptance(p)["achieved"]


def test_revision_compare_and_swap_and_persistence(app, planned):
    first = app.db.get(planned.id)
    second = app.db.get(planned.id)
    first.name = "Saved"
    app.db.save(first, "rename")
    with pytest.raises(ConflictError):
        app.db.save(second, "stale")
    reopened = Database(app.db.path)
    assert reopened.get(planned.id).name == "Saved"


def test_dependency_cycle_and_missing_reason_rejected():
    plan = proposal()
    plan.milestones[0].dependencies = ["M01"]
    plan.milestones[0].dependency_reasons = {"M01": "cycle"}
    with pytest.raises(ValueError, match="环"):
        validate_plan(plan)
    plan.milestones[0].dependency_reasons = {}
    with pytest.raises(ValueError, match="理由"):
        validate_plan(plan)


def test_duplicate_behavior_ownership_is_rejected():
    plan = proposal()
    node = plan.milestones[0].model_copy(deep=True)
    node.id = "M02"
    plan.milestones.append(node)
    with pytest.raises(ValueError, match="行为 key"):
        validate_plan(plan)


def test_cochange_is_advisory_not_transitive_hard_closure():
    closure, candidates = impact_closure(
        {"schema"},
        [
            {"from": "schema", "to": "api", "type": "reads_schema"},
            {"from": "api", "to": "ui", "type": "cochange"},
            {"from": "ui", "to": "analytics", "type": "imports"},
        ],
    )
    assert closure == {"schema", "api"}
    assert candidates == {"ui"}
    assert retirement_disposition(False, False) == "UNKNOWN"


def test_required_obligation_needs_actual_note(app, planned):
    with pytest.raises(ValueError, match="依据"):
        app.execution.resolve_obligation(planned.id, "M01", "scope", True, "")


def test_incomplete_baseline_never_supports_acceptance(app, planned):
    p = app.db.get(planned.id)
    p.baselines[-1].complete = False
    assert not readiness(p, "M01")["safe_to_execute"]


def test_stale_proposal_cannot_overwrite_a_newer_project(app, planned):
    p = app.db.get(planned.id)
    p.proposal = proposal("V2", "New statement", "M02")
    p.proposal_revision = p.revision + 1
    p = app.db.save(p, "proposal")
    original = p.revision
    app.db.save(p, "other_edit")
    with pytest.raises(ValueError, match="改变"):
        app.planning.apply(p.id, original)
