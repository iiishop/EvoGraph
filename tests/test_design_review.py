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
