import asyncio

import pytest
from conftest import external_report
from evograph.application.uml import render, validate_source
from evograph.domain.models import MigrationStep, UmlDiagram
from test_design import architecture


def test_external_workflow_pass_releases_and_rejects_replay(app, planned):
    with pytest.raises(ValueError, match="领取"):
        app.acceptance.prepare(planned.id, "M01")
    app.execution.start(planned.id, "M01")
    assert "auth.py" in app.acceptance.implementation(planned.id, "M01")["prompt"]
    req = app.acceptance.prepare(planned.id, "M01")
    report = external_report(app, planned.id, "M01", req["request_id"])
    result = app.acceptance.import_report(planned.id, "M01", report)
    assert result["released"]
    m = app.db.get(planned.id).milestone("M01")
    assert m.status == "VERIFIED_COMPLETE" and not m.lease_active
    assert app.projects.get(planned.id)["acceptance"]["achieved"]
    with pytest.raises(ValueError, match="已处理"):
        app.acceptance.import_report(planned.id, "M01", report)
    assert len(app.db.get(planned.id).evidence) == 1


def test_external_fail_retains_lease_and_can_retry(app, planned):
    app.execution.start(planned.id, "M01")
    req = app.acceptance.prepare(planned.id, "M01")
    report = external_report(app, planned.id, "M01", req["request_id"], "FAIL")
    assert not app.acceptance.import_report(planned.id, "M01", report)["released"]
    m = app.db.get(planned.id).milestone("M01")
    assert m.lease_active and m.status == "IN_PROGRESS"
    again = app.acceptance.prepare(planned.id, "M01")
    assert again["request_id"] != req["request_id"]


def test_stale_report_missing_coverage_and_wrong_task_rejected(app, planned, repository):
    app.execution.start(planned.id, "M01")
    req = app.acceptance.prepare(planned.id, "M01")
    report = external_report(app, planned.id, "M01", req["request_id"])
    report.checks.append(report.checks[0])
    with pytest.raises(ValueError, match="逐项"):
        app.acceptance.import_report(planned.id, "M01", report)
    report.checks.pop()
    report.request_id = "wrong"
    with pytest.raises(ValueError, match="不属于"):
        app.acceptance.import_report(planned.id, "M01", report)
    report.request_id = req["request_id"]
    (repository / "auth.py").write_text("def login(): return False")
    with pytest.raises(ValueError, match="基线"):
        app.acceptance.import_report(planned.id, "M01", report)
    assert not app.db.get(planned.id).evidence
    assert app.db.get(planned.id).milestone("M01").lease_active


def test_release_invalidates_request_and_local_execution_api_is_gone(app, planned):
    app.execution.start(planned.id, "M01")
    req = app.acceptance.prepare(planned.id, "M01")
    app.execution.release(planned.id, "M01")
    with pytest.raises(ValueError):
        app.acceptance.import_report(
            planned.id, "M01", external_report(app, planned.id, "M01", req["request_id"])
        )
    assert asyncio.run(app.dispatch("milestone.verify", {}))["error"]["code"] == "UNKNOWN_ACTION"


def test_architecture_retirement_becomes_step_preserving_history(app, planned):
    app.design.update(planned.id, architecture())
    change = architecture()
    change.diagram.nodes[0].id = "next_api"
    change.retirements = [
        MigrationStep(component_id="api", milestone_id="M01", instruction="迁移调用方后删除旧 API")
    ]
    app.design.update(planned.id, change)
    p = app.db.get(planned.id)
    assert p.architectures[0].diagram.nodes[0].id == "api"
    assert p.architectures[-1].diagram.nodes[0].id == "next_api"
    assert p.milestone("M01").migration_steps[0].from_revision == 1


SOURCE = """@startuml
skinparam packageStyle rectangle
skinparam linetype polyline
hide empty members
' Commit: fixture
' Scope: backend
package BackEnd {
class Auth <<Module>> {
 + login() : bool
}
class Result <<Data Contract>> {
 + success : bool
}
Auth::login --> Result : Create >
note right of Auth::login
  测试模块的真实返回约束。
end note
}
@enduml"""


def test_uml_history_single_class_and_inert_source(app, planned, monkeypatch):
    monkeypatch.setattr("evograph.application.uml.render", lambda source: b"<svg/>")
    d = UmlDiagram(
        id="overview",
        title="总览",
        kind="class",
        source=SOURCE,
        scope="backend",
        origin="source",
        source_refs=["auth.py"],
    )
    app.uml.save(planned.id, d)
    app.uml.save(planned.id, d.model_copy(update={"title": "总览更新"}))
    p = app.db.get(planned.id)
    assert [d.id for d in p.uml_diagrams] == ["class_model", "class_model"]
    assert [d.revision for d in p.uml_diagrams] == [1, 2]
    for bad in [
        "!include /secret",
        "!includeurl https://example.com",
        '%getenv("SECRET")',
        "[[https://example.com]]",
    ]:
        with pytest.raises(ValueError):
            validate_source("@startuml\n" + bad + "\n@enduml")


def test_plantuml_compiles_member_relations_locally():
    import shutil
    from pathlib import Path

    if not shutil.which("java") or not Path(".tools/plantuml.jar").is_file():
        pytest.skip("Optional local PlantUML runtime is not installed")
    assert b"<svg" in render(SOURCE)
