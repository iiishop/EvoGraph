from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.domain.design_review import review_design
from evograph.domain.models import ArchitectureRevision


def test_review_is_readonly_and_reports_broken_contracts(planned):
    m = planned.milestones[0]
    m.dependencies = ["missing"]
    m.behavior_revision_ids = ["missing"]
    before = planned.model_dump()
    report = review_design(planned)
    assert {f["code"] for f in report["findings"]} >= {
        "missing_dependency",
        "invalid_behavior",
        "architecture_missing",
    }
    assert report["project_revision"] == planned.revision
    assert planned.model_dump() == before


def test_review_detects_architecture_drift_and_preserves_advisory_semantics(planned):
    planned.architectures = [
        ArchitectureRevision(
            number=2,
            summary="API design",
            technologies=[{"area": "API", "choice": "Python", "rationale": "Existing stack"}],
            diagram={"id": "system", "title": "System", "nodes": [{"id": "api", "label": "API"}]},
        )
    ]
    planned.milestones[0].architecture_components = ["retired"]
    report = review_design(planned)
    findings = {f["code"]: f for f in report["findings"]}
    assert findings["unknown_component"]["severity"] == "error"
    assert findings["stale_architecture"]["severity"] == "review"
    assert "component_responsibility" in findings
    assert "architecture_quality_scenarios" in findings
    assert report["semantic_review"]


def test_review_tool_registered_and_does_not_mutate(app, planned):
    spec = tools()["review_design"]
    before = app.db.get(planned.id).model_dump()
    report = spec.handler(ToolContext(planned.id, app), spec.parameters())
    assert report["status"] == "needs_review"
    assert spec.effect == "inspect"
    assert app.db.get(planned.id).model_dump() == before


def test_empty_project_is_not_a_design_failure(app):
    project = app.projects.create("Empty")
    report = review_design(project)
    assert report["status"] == "structural_checks_clear"
    assert report["findings"] == []
    assert "不代表验收通过" in report["limitation"]


def test_review_flags_stage_only_plan_without_reclassifying_behaviors(planned):
    for behavior in planned.behaviors:
        behavior.acceptance_scope = "milestone"
    before = planned.model_dump()
    report = review_design(planned)
    warning = next(f for f in report["findings"] if f["code"] == "target_coverage_missing")
    assert warning["severity"] == "review"
    assert "最终目标标准" in warning["message"]
    assert planned.model_dump() == before


def test_review_ignores_historical_target_behaviors_when_current_plan_is_stage_only(planned):
    from evograph.domain.models import BehaviorRevision

    planned.behaviors.append(
        BehaviorRevision(
            behavior_key="retired.target", version=1, statement="Past target", owner="removed"
        )
    )
    for behavior in planned.behaviors:
        if behavior.id in planned.milestones[0].behavior_revision_ids:
            behavior.acceptance_scope = "milestone"
    codes = {f["code"] for f in review_design(planned)["findings"]}
    assert "target_coverage_missing" in codes


def test_review_accepts_mixed_target_and_step_coverage(planned):
    from evograph.domain.models import BehaviorRevision

    temporary = BehaviorRevision(
        behavior_key="step.csv",
        version=1,
        statement="CSV writes work during migration",
        owner=planned.milestones[0].id,
        acceptance_scope="milestone",
    )
    planned.behaviors.append(temporary)
    planned.milestones[0].behavior_revision_ids.append(temporary.id)
    assert "target_coverage_missing" not in {f["code"] for f in review_design(planned)["findings"]}


def test_review_exposes_actual_target_membership_despite_optional_prose(planned):
    from evograph.domain.models import BehaviorRevision

    milestone = planned.milestones[0]
    milestone.title = "Optional leaf"
    milestone.intent = "This can be skipped"
    local = BehaviorRevision(
        behavior_key="local.check", version=1, statement="Transitional check",
        owner=milestone.id, acceptance_scope="milestone",
    )
    planned.behaviors.append(local)
    milestone.behavior_revision_ids.append(local.id)
    before = planned.model_dump()
    membership = review_design(planned)["prospective_target_membership"]
    row = membership["milestones"][0]
    assert row["milestone_id"] == milestone.id
    assert row["target_behavior_count"] == 1
    assert row["milestone_behavior_count"] == 1
    assert row["missing_behavior_count"] == 0
    assert membership["target_behavior_count"] == 1
    assert planned.model_dump() == before


def test_membership_preview_counts_active_revisions_not_old_targets(planned):
    from evograph.domain.models import BehaviorRevision

    for behavior in planned.behaviors:
        behavior.acceptance_scope = "milestone"
    planned.behaviors.append(BehaviorRevision(
        behavior_key="retired.target", version=1, statement="Historical target",
        owner="removed",
    ))
    planned.milestones[0].behavior_revision_ids.append("missing")
    report = review_design(planned)
    membership = report["prospective_target_membership"]
    assert membership["target_behavior_count"] == 0
    assert membership["milestones"][0]["missing_behavior_count"] == 1
    assert any(f["code"] == "invalid_behavior" for f in report["findings"])
